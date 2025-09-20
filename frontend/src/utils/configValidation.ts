import { z } from 'zod';

/**
 * Config Validation (Zod)
 * - Mirrors backend /config payload
 * - Matches src/types/config.ts
 * - Adds sensible coercions and refinements
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
  // Require both maps to exist per src/types/config.ts
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
  landingPage: z.record(z.string(), z.any()).optional(),
  footer: FooterSchema,
  // Keep these generic to avoid over-constraining content blocks from the backend
  loadingStates: z.record(z.string(), z.any()).optional(),
  errors: z.record(z.string(), z.any()).optional(),
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

// --- Validation Function ---
export function validateAndNormalizeConfig(rawConfig: unknown): AppConfig {
  try {
    return AppConfigSchema.parse(rawConfig);
  } catch (error) {
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error('❌ Invalid application configuration:', error.flatten().fieldErrors);
    }
    throw new Error('Application configuration is invalid and could not be parsed.');
  }
}
