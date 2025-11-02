// frontend/src/utils/configValidation.ts
import { z } from 'zod';
import { DEFAULT_APP_CONFIG } from '../config/defaultAppConfig';
import type { AppConfig } from '../types/config';

/**
 * Validation + merge (backend overrides default app config).
 * Schemas are for runtime validation only. No defaults defined here.
 */

/* ------------------------ Reusable Schemas ------------------------ */

const LinkSchema = z.object({
  label: z.string(),
  href: z.string(),
  external: z.boolean().optional(),
}).strict();

const FooterSchema = z.object({
  about: LinkSchema,
  terms: LinkSchema,
  privacy: LinkSchema,
  donate: LinkSchema,
  copyright: z.string().optional(),
}).strict();

const StaticBlockSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('p'),  text: z.string() }).strict(),
  z.object({ type: z.literal('h2'), text: z.string() }).strict(),
  z.object({ type: z.literal('ul'), items: z.array(z.string()) }).strict(),
  z.object({ type: z.literal('ol'), items: z.array(z.string()) }).strict(),
]);

// Optional description/blocks to match defaults/backend
const StaticPageSchema = z.object({
  title: z.string(),
  description: z.string().optional(),
  blocks: z.array(StaticBlockSchema).optional(),
}).strict();

const ResultPageSchema = z.object({
  titlePrefix: z.string().optional(),
  shareButton: z.string().optional(),
  shareCopied: z.string().optional(),
  startOverButton: z.string().optional(),
  traitListTitle: z.string().optional(),

  // direct share + fallback labels
  shareText: z.string().optional(),
  shared: z.string().optional(),
  copyLink: z.string().optional(),

  feedback: z.object({
    prompt: z.string().optional(),
    thumbsUp: z.string().optional(),
    thumbsDown: z.string().optional(),
    commentPlaceholder: z.string().optional(),
    submit: z.string().optional(),
    thanks: z.string().optional(),
    turnstileError: z.string().optional(),
  }).partial().optional(),

  share: z.object({
    socialTitle: z.string().optional(),
    socialDescription: z.string().optional(),
  }).partial().optional(),
}).strict().partial();

const ErrorsSchema = z.object({
  title: z.string().optional(),
  description: z.string().optional(),
  retry: z.string().optional(),
  home: z.string().optional(),
  startOver: z.string().optional(),
  categoryNotFound: z.string().optional(),
  requestTimeout: z.string().optional(),
  quizCreationFailed: z.string().optional(),
  resultNotFound: z.string().optional(),
  hideDetails: z.string().optional(),
  details: z.string().optional(),
  submissionFailed: z.string().optional(),
}).strict();

const LoadingStatesSchema = z.object({
  page: z.string().optional(),
  question: z.string().optional(),
  quiz: z.string().optional(),
}).strict();

const NotFoundPageSchema = z.object({
  heading: z.string().optional(),
  subheading: z.string().optional(),
  buttonText: z.string().optional(),
}).strict();

/* ------------------------ Theme Schemas ------------------------ */
// Accept arbitrary string tokens under theme.layout.landing (future-proof)
const LandingLayoutSchema = z.record(z.string(), z.string());

const ThemeSchemaStrict = z.object({
  colors: z.record(z.string(), z.string()),
  fonts:  z.record(z.string(), z.string()),
  fontSizes: z.record(z.string(), z.string()).optional(),
  dark: z.object({
    colors: z.record(z.string(), z.string()),
  }).strict().optional(),
  layout: z.object({
    landing: LandingLayoutSchema.optional(),
  }).partial().optional(),
}).strict();

/* ------------------------ Content Schema ------------------------ */

const ContentSchemaStrict = z.object({
  appName: z.string(),
  landingPage: z.record(z.string(), z.any()), // stays flexible
  footer: FooterSchema,
  aboutPage: StaticPageSchema,
  termsPage: StaticPageSchema,
  privacyPolicyPage: StaticPageSchema,
  resultPage: ResultPageSchema.optional(),
  errors: ErrorsSchema, // we expect this after merge (defaults always provide)
  notFoundPage: NotFoundPageSchema.optional(),
  loadingStates: LoadingStatesSchema.optional(),
}).strict();

/* ------------------------ Limits / Timeouts / Features ------------------------ */

const LimitsSchemaStrict = z.object({
  validation: z.object({
    category_min_length: z.coerce.number().int().positive(),
    category_max_length: z.coerce.number().int().positive(),
  }).strict().refine(
    (v) => v.category_max_length >= v.category_min_length,
    { message: 'category_max_length must be >= category_min_length', path: ['category_max_length'] }
  ),
}).strict();

const ApiTimeoutsSchemaStrict = z.object({
  default: z.coerce.number().int().positive(),
  startQuiz: z.coerce.number().int().positive(),
  poll: z.object({
    total: z.coerce.number().int().positive(),
    interval: z.coerce.number().int().nonnegative(),
    maxInterval: z.coerce.number().int().positive(),
  }).strict().refine(
    (p) => p.maxInterval >= p.interval,
    { message: 'poll.maxInterval must be >= poll.interval', path: ['maxInterval'] }
  ),
}).strict();

/**
 * Authoritative flag is `turnstile`.
 * `turnstileEnabled` is a legacy mirror (optional here).
 */
const FeaturesSchemaStrict = z.object({
  turnstile: z.boolean(),
  turnstileEnabled: z.boolean().optional(),
  turnstileSiteKey: z.string().optional(),
}).strict();

/* ------------------------ Final + Partial Schemas ------------------------ */

const AppConfigSchemaStrict = z.object({
  theme: ThemeSchemaStrict,
  content: ContentSchemaStrict,
  limits: LimitsSchemaStrict,
  apiTimeouts: ApiTimeoutsSchemaStrict,
  features: FeaturesSchemaStrict.optional(),
}).strict();

// Loose/partial version for backend payload
const AppConfigSchemaPartial = z.object({
  theme: z.object({
    colors: z.record(z.string(), z.string()).optional(),
    fonts:  z.record(z.string(), z.string()).optional(),
    fontSizes: z.record(z.string(), z.string()).optional(),
    dark: z.object({
      colors: z.record(z.string(), z.string()),
    }).strict().optional(),
    layout: z.object({
      landing: LandingLayoutSchema.optional(),
    }).partial().optional(),
  }).partial().optional(),

  content: z.object({
    appName: z.string().optional(),
    landingPage: z.record(z.string(), z.any()).optional(),
    footer: FooterSchema.partial().optional(),
    aboutPage: StaticPageSchema.partial().optional(),
    termsPage: StaticPageSchema.partial().optional(),
    privacyPolicyPage: StaticPageSchema.partial().optional(),
    resultPage: ResultPageSchema.optional(),
    errors: ErrorsSchema.optional(),
    notFoundPage: NotFoundPageSchema.optional(),
    loadingStates: LoadingStatesSchema.optional(),
  }).partial().optional(),

  limits: z.object({
    validation: z.object({
      category_min_length: z.coerce.number().int().positive().optional(),
      category_max_length: z.coerce.number().int().positive().optional(),
    }).partial().optional(),
  }).partial().optional(),

  apiTimeouts: z.object({
    default: z.coerce.number().int().positive().optional(),
    startQuiz: z.coerce.number().int().positive().optional(),
    poll: z.object({
      total: z.coerce.number().int().positive().optional(),
      interval: z.coerce.number().int().nonnegative().optional(),
      maxInterval: z.coerce.number().int().positive().optional(),
    }).partial().optional(),
  }).partial().optional(),

  features: z.object({
    turnstile: z.boolean().optional(),
    turnstileEnabled: z.boolean().optional(),
    turnstileSiteKey: z.string().optional(),
  }).partial().optional(),
}).partial().strict();

/* ------------------------ Merge Helpers ------------------------ */

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return Object.prototype.toString.call(v) === '[object Object]';
}

/** Deep merge `b` into `a`. Arrays are replaced; null/undefined in `b` are ignored. */
function deepMerge<T>(a: T, b: any): T {
  if (!isPlainObject(a) || !isPlainObject(b)) return (b ?? a) as T;
  const out: Record<string, unknown> = { ...a };
  for (const key of Object.keys(b)) {
    const bv = (b as any)[key];
    if (bv === undefined || bv === null) continue;
    const av = (a as any)[key];
    if (Array.isArray(bv)) {
      out[key] = bv;
    } else if (isPlainObject(av) && isPlainObject(bv)) {
      out[key] = deepMerge(av, bv);
    } else {
      out[key] = bv;
    }
  }
  return out as T;
}

/* ------------------------ Post-merge feature alignment ------------------------ */

/** Ensure both `turnstile` and `turnstileEnabled` are present and identical. */
function alignTurnstileFlags(cfg: AppConfig): AppConfig {
  const f = cfg.features ?? {};
  const hasTurnstile = typeof (f as any).turnstile === 'boolean';
  const hasEnabled   = typeof (f as any).turnstileEnabled === 'boolean';

  const value = hasTurnstile
    ? (f as any).turnstile as boolean
    : hasEnabled
      ? (f as any).turnstileEnabled as boolean
      : true;

  return {
    ...cfg,
    features: {
      ...f,
      turnstile: value,
      turnstileEnabled: value,
    },
  };
}

/* ------------------------ Public API ------------------------ */

/**
 * Validate backend config (loosely), merge OVER defaults,
 * then strictly validate the merged result to produce AppConfig.
 */
export function validateAndNormalizeConfig(rawConfig: unknown): AppConfig {
  // 1) Parse backend payload loosely (allow missing keys)
  let partial: z.infer<typeof AppConfigSchemaPartial>;
  try {
    partial = AppConfigSchemaPartial.parse(rawConfig ?? {});
  } catch (error) {
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error('❌ Invalid backend configuration:', error.flatten().fieldErrors);
    }
    throw new Error('Application configuration is invalid and could not be parsed.');
  }

  // 2) Merge backend OVER defaults (defaults come ONLY from DEFAULT_APP_CONFIG)
  const merged = deepMerge(DEFAULT_APP_CONFIG, partial);

  // 3) Strictly validate the final shape
  let validated: AppConfig;
  try {
    validated = AppConfigSchemaStrict.parse(merged) as AppConfig;
  } catch (error) {
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error('❌ Merged configuration failed strict validation:', error.flatten().fieldErrors);
    }
    throw new Error('Merged application configuration failed validation.');
  }

  // 4) Align features flags (authoritative = `turnstile`)
  return alignTurnstileFlags(validated);
}
