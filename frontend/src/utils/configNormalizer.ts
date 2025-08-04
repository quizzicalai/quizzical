// src/utils/configNormalizer.ts
import { z } from 'zod';

// --- Reusable Schemas ---
const FooterLinkSchema = z.object({
  label: z.string().min(1),
  href: z.string().min(1),
  external: z.boolean().optional(),
});

const StaticBlockSchema = z.union([
  z.object({ type: z.literal('p'), text: z.string() }),
  z.object({ type: z.literal('h2'), text: z.string() }),
  z.object({ type: z.literal('ul'), items: z.array(z.string()) }),
  z.object({ type: z.literal('ol'), items: z.array(z.string()) }),
]);

const StaticPageSchema = z.object({
  title: z.string(),
  blocks: z.array(StaticBlockSchema),
});

// --- Main Schemas ---
const ThemeConfigSchema = z.object({
  colors: z.record(z.string(), z.string()),
  fonts: z.record(z.string(), z.string()),
  dark: z.object({
    colors: z.record(z.string(), z.string()),
  }).optional(),
});

const ContentConfigSchema = z.object({
  appName: z.string(),
  landingPage: z.record(z.string(), z.any()),
  footer: z.object({
    about: FooterLinkSchema,
    terms: FooterLinkSchema,
    privacy: FooterLinkSchema,
    donate: FooterLinkSchema,
    copyright: z.string().optional(),
  }),
  aboutPage: StaticPageSchema,
  termsPage: StaticPageSchema,
  privacyPolicyPage: StaticPageSchema,
  resultPage: z.record(z.string(), z.any()).optional(),
  errors: z.record(z.string(), z.any()).optional(),
  notFoundPage: z.record(z.string(), z.any()).optional(),
});

const LimitsConfigSchema = z.object({
  validation: z.object({
    category_min_length: z.number(),
    category_max_length: z.number(),
  }),
});

export const AppConfigSchema = z.object({
  theme: ThemeConfigSchema,
  content: ContentConfigSchema,
  limits: LimitsConfigSchema,
});

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
