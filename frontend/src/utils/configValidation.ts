// src/utils/configValidation.ts
import { z } from 'zod';

// --- Reusable Schemas ---
const LinkSchema = z.object({
  label: z.string(),
  href: z.string(),
  external: z.boolean().optional(),
});

const FooterSchema = z.object({
  about: LinkSchema.optional(),
  terms: LinkSchema.optional(),
  privacy: LinkSchema.optional(),
  donate: LinkSchema.optional(),
  copyright: z.string().optional(),
});

const StaticBlockSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("p"), text: z.string() }),
  z.object({ type: z.literal("h2"), text: z.string() }),
  z.object({ type: z.literal("ul"), items: z.array(z.string()) }),
  z.object({ type: z.literal("ol"), items: z.array(z.string()) }),
]);

const StaticPageSchema = z.object({
  title: z.string(),
  blocks: z.array(StaticBlockSchema),
});

// --- Main Schemas ---
const ThemeConfigSchema = z.object({
  colors: z.record(z.string(), z.string()),
  fonts: z.record(z.string(), z.string()).optional(),
  dark: z.object({
    colors: z.record(z.string(), z.string()),
  }).optional(),
});

const ContentConfigSchema = z.object({
  appName: z.string(),
  landingPage: z.record(z.string(), z.any()).optional(),
  footer: FooterSchema,
  loadingStates: z.record(z.string(), z.any()).optional(),
  errors: z.record(z.string(), z.any()).optional(),
  aboutPage: StaticPageSchema.optional(),
  termsPage: StaticPageSchema.optional(),
  privacyPolicyPage: StaticPageSchema.optional(),
  resultPage: z.record(z.string(), z.any()).optional(),
  notFoundPage: z.record(z.string(), z.any()).optional(),
});

const LimitsConfigSchema = z.object({
  validation: z.object({
    category_min_length: z.number(),
    category_max_length: z.number(),
  }),
});

// New: Schema for API timeouts
const ApiTimeoutsSchema = z.object({
  default: z.number().int().positive(),
  startQuiz: z.number().int().positive(),
  poll: z.object({
    total: z.number().int().positive(),
    interval: z.number().int().positive(),
    maxInterval: z.number().int().positive(),
  }),
});

const FeaturesSchema = z.object({
  turnstileEnabled: z.boolean(),
  turnstileSiteKey: z.string().optional(),
});

const AppConfigSchema = z.object({
  theme: ThemeConfigSchema,
  content: ContentConfigSchema,
  limits: LimitsConfigSchema,
  apiTimeouts: ApiTimeoutsSchema,
  features: FeaturesSchema.optional(), // <— optional keeps backwards compat
}).strict();

// --- Inferred Type (Single Source of Truth) ---
export type AppConfig = z.infer<typeof AppConfigSchema>;

// --- Validation Function ---
export function validateAndNormalizeConfig(rawConfig: unknown): AppConfig {
  try {
    return AppConfigSchema.parse(rawConfig);
  } catch (error) {
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error("❌ Invalid application configuration:", error.flatten().fieldErrors);
    }
    throw new Error("Application configuration is invalid and could not be parsed.");
  }
}
