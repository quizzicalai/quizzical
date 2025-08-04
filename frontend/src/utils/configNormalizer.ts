// src/utils/configNormalizer.ts
import { z } from 'zod';

// Zod schema for a single footer link
const FooterLinkSchema = z.object({
  label: z.string().min(1),
  href: z.string().min(1),
  external: z.boolean().optional(),
});

// Zod schema for a single block of static content
const StaticBlockSchema = z.union([
  z.object({ type: z.literal('p'), text: z.string() }),
  z.object({ type: z.literal('h2'), text: z.string() }),
  z.object({ type: z.literal('ul'), items: z.array(z.string()) }),
  z.object({ type: z.literal('ol'), items: z.array(z.string()) }),
]);

// Zod schema for a full static page
const StaticPageSchema = z.object({
  title: z.string(),
  blocks: z.array(StaticBlockSchema),
});

// Zod schema for the complete content configuration
const ContentConfigSchema = z.object({
  appName: z.string(),
  landingPage: z.record(z.string(), z.any()), // FIXED: z.record now has key and value types
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
  resultPage: z.object({
    titlePrefix: z.string().optional(),
    shareButton: z.string(),
    shareCopied: z.string(),
    startOverButton: z.string(),
    traitListTitle: z.string().optional(),
    feedback: z.object({ /* ... can be further detailed */ }),
  }),
  errors: z.object({
    title: z.string(),
    retry: z.string(),
    home: z.string(),
    startOver: z.string(),
    // ... can be further detailed
  }),
});

// The top-level zod schema for the entire app configuration
export const AppConfigSchema = z.object({
  theme: z.record(z.string(), z.any()), // FIXED: z.record now has key and value types
  content: ContentConfigSchema,
  limits: z.record(z.string(), z.any()), // FIXED: z.record now has key and value types
});

// Infer the TypeScript type from the Zod schema
export type AppConfig = z.infer<typeof AppConfigSchema>;


/**
 * Validates the raw configuration object against the Zod schema.
 * Logs detailed errors in development if validation fails.
 * @param rawConfig - The raw, untyped configuration object.
 * @returns The validated and typed configuration.
 * @throws {Error} If the configuration is invalid.
 */
export function validateAndNormalizeConfig(rawConfig: unknown): AppConfig { // FIXED: Explicitly type rawConfig
  try {
    const validatedConfig = AppConfigSchema.parse(rawConfig);
    // Future normalization logic could go here, e.g., for backward compatibility.
    return validatedConfig;
  } catch (error) { // FIXED: Check if the error is a ZodError before using its methods
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error("❌ Invalid application configuration:", error.flatten().fieldErrors);
    }
    throw new Error("Application configuration is invalid and could not be parsed.");
  }
}