import { z } from 'zod';

/**
 * Config Validation (Zod) + Normalization
 * - Mirrors backend /config payload
 * - Matches src/types/config.ts
 * - Adds sensible coercions and **fills runtime defaults**
 *   so the UI always has copy (and a heading) even when
 *   the backend sends minimal content (e.g., landingPage: {}).
 *
 * All defaults are overrideable by backend config.
 */

// --- Reusable Schemas ---
const LinkSchema = z.object({
  label: z.string(),
  href: z.string(),
  external: z.boolean().optional(),
}).strict();

const FooterSchema = z.object({
  about: LinkSchema.optional(),
  terms: LinkSchema.optional(),
  privacy: LinkSchema.optional(),
  donate: LinkSchema.optional(),
  copyright: z.string().optional(),
}).strict();

const StaticBlockSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('p'),  text: z.string() }).strict(),
  z.object({ type: z.literal('h2'), text: z.string() }).strict(),
  z.object({ type: z.literal('ul'), items: z.array(z.string()) }).strict(),
  z.object({ type: z.literal('ol'), items: z.array(z.string()) }).strict(),
]);

const StaticPageSchema = z.object({
  title: z.string(),
  blocks: z.array(StaticBlockSchema),
}).strict();

// --- Main Schemas ---
const ThemeConfigSchema = z.object({
  colors: z.record(z.string(), z.string()),
  fonts: z.record(z.string(), z.string()),
  dark: z
    .object({
      colors: z.record(z.string(), z.string()),
    })
    .strict()
    .optional(),
}).strict();

const ContentConfigSchema = z.object({
  appName: z.string(),
  landingPage: z.record(z.string(), z.any()).default({}),
  footer: FooterSchema,
  loadingStates: z.record(z.string(), z.any()).default({}),
  errors: z.record(z.string(), z.any()).default({}),
  aboutPage: StaticPageSchema.optional(),
  termsPage: StaticPageSchema.optional(),
  privacyPolicyPage: StaticPageSchema.optional(),
  resultPage: z.record(z.string(), z.any()).optional(),
  notFoundPage: z.record(z.string(), z.any()).optional(),
}).strict();

const LimitsConfigSchema = z.object({
  validation: z
    .object({
      // Coerce to numbers so "100" works in local YAML/dev
      category_min_length: z.coerce.number().int().positive(),
      category_max_length: z.coerce.number().int().positive(),
    })
    .strict()
    .refine(
      (v) => v.category_max_length >= v.category_min_length,
      { message: 'category_max_length must be >= category_min_length', path: ['category_max_length'] }
    ),
}).strict();

// API timeouts: coerce integers and ensure positivity
const ApiTimeoutsSchema = z.object({
  default: z.coerce.number().int().positive(),
  startQuiz: z.coerce.number().int().positive(),
  poll: z
    .object({
      total: z.coerce.number().int().positive(),
      interval: z.coerce.number().int().nonnegative(), // 0 is allowed (immediate first poll)
      maxInterval: z.coerce.number().int().positive(),
    })
    .strict()
    .refine(
      (p) => p.maxInterval >= p.interval,
      { message: 'poll.maxInterval must be >= poll.interval', path: ['maxInterval'] }
    ),
}).strict();

const FeaturesSchema = z.object({
  turnstileEnabled: z.boolean(),
  turnstileSiteKey: z.string().optional(),
}).strict();

const AppConfigSchema = z.object({
  theme: ThemeConfigSchema,
  content: ContentConfigSchema,
  limits: LimitsConfigSchema,
  apiTimeouts: ApiTimeoutsSchema,
  features: FeaturesSchema.optional(), // optional for backwards-compat
}).strict();

// --- Inferred Type (Single Source of Truth) ---
export type AppConfig = z.infer<typeof AppConfigSchema>;

// --- Runtime Defaults (all overrideable by backend) ---
const DEFAULTS = {
  content: {
    landingPage: {
      title: 'Unlock Your Inner Persona',
      subtitle: 'Answer a few questions and let our AI reveal a surprising profile of you.',
      inputPlaceholder: "e.g., 'Ancient Rome', 'Baking'",
      submitButton: 'Create My Quiz',
      inputAriaLabel: 'Quiz category input',
      examples: ['Ancient Rome', 'Baking'],
      // Validation messages used by <InputGroup/>
      validation: {
        minLength: 'Must be at least {min} characters.',
        maxLength: 'Cannot exceed {max} characters.',
      },
    },
    loadingStates: {
      page: 'Loading...',
      quiz: 'Preparing your quiz...',
      question: 'Thinking...',
    },
    errors: {
      title: 'An Error Occurred',
      description: 'Something went wrong. Please try again or return to the home page.',
      requestTimeout: "The request timed out. It's taking longer than expected.",
      quizCreationFailed: 'We were unable to create your quiz at this time.',
      categoryNotFound: "Sorry, we couldn't create a quiz for that category. Please try a different one.",
      resultNotFound: 'This result could not be found.',
      startOver: 'Start Over',
      retry: 'Try Again',
      home: 'Go Home',
    },
  },
} as const;

/**
 * Validate then **normalize** with defaults.
 * - Ensures LandingPage has title/subtitle/etc. even when backend sends landingPage: {}
 * - All defaults are shallow-merged and can be overridden by backend.
 */
export function validateAndNormalizeConfig(rawConfig: unknown): AppConfig {
  let parsed: AppConfig;
  try {
    parsed = AppConfigSchema.parse(rawConfig);
  } catch (error) {
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error('❌ Invalid application configuration:', error.flatten().fieldErrors);
    }
    throw new Error('Application configuration is invalid and could not be parsed.');
  }

  const lp = parsed.content.landingPage ?? {};
  const loading = parsed.content.loadingStates ?? {};
  const errs = parsed.content.errors ?? {};

  // Shallow-merge defaults so backend can override any key
  const normalized: AppConfig = {
    ...parsed,
    content: {
      ...parsed.content,
      landingPage: {
        ...DEFAULTS.content.landingPage,
        ...lp,
        // nested validation defaults
        validation: {
          ...(DEFAULTS.content.landingPage.validation || {}),
          ...(lp.validation ?? {}),
        },
      },
      loadingStates: {
        ...DEFAULTS.content.loadingStates,
        ...loading,
      },
      errors: {
        ...DEFAULTS.content.errors,
        ...errs,
      },
    },
  };

  return normalized;
}
