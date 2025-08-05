import { z } from 'zod';

// Schema for a single link, used in footers or other navigation
const LinkSchema = z.object({
  label: z.string(),
  href: z.string(),
  external: z.boolean().optional(),
});

// Schema for the footer, preferring named keys for deterministic rendering
const FooterSchema = z.object({
  about: LinkSchema.optional(),
  terms: LinkSchema.optional(),
  privacy: LinkSchema.optional(),
  donate: LinkSchema.optional(),
  copyright: z.string().optional(),
});

// Schemas for dynamic UI text based on application state
const LoadingStatesSchema = z.object({
  page: z.string().optional(),
  question: z.string().optional(),
  quiz: z.string().optional(),
});

const ErrorsSchema = z.object({
  title: z.string().optional(),
  description: z.string().optional(),
  requestTimeout: z.string().optional(),
  quizCreationFailed: z.string().optional(),
  categoryNotFound: z.string().optional(),
  resultNotFound: z.string().optional(),
  sessionExpired: z.string().optional(), // Added missing property
  startOver: z.string().optional(),
  details: z.string().optional(),
  hideDetails: z.string().optional(),
  showDetails: z.string().optional(),
});

// A discriminated union for static content blocks to ensure type safety.
export const StaticBlockSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("p"), text: z.string() }),
  z.object({ type: z.literal("h2"), text: z.string() }),
  z.object({ type: z.literal("ul"), items: z.array(z.string()) }),
  z.object({ type: z.literal("ol"), items: z.array(z.string()) }),
]);

const StaticPageSchema = z.object({
  title: z.string(),
  blocks: z.array(StaticBlockSchema),
});

// Schema for the theme, including colors and fonts
const ThemeColorsSchema = z.record(z.string(), z.string());
const ThemeSchema = z.object({
  colors: ThemeColorsSchema,
  dark: z.object({ colors: ThemeColorsSchema }).optional(),
  fonts: z.record(z.string(), z.string()).optional(),
});

// The single source of truth for the entire application configuration
export const AppConfigSchema = z.object({
  content: z.object({
    appName: z.string(),
    landingPage: z.record(z.string(), z.any()).optional(),
    footer: FooterSchema,
    loadingStates: LoadingStatesSchema.optional(),
    errors: ErrorsSchema.optional(),
    aboutPage: StaticPageSchema.optional(),
    termsPage: StaticPageSchema.optional(),
    privacyPolicyPage: StaticPageSchema.optional(),
    resultPage: z.record(z.string(), z.any()).optional(),
    notFoundPage: z.record(z.string(), z.any()).optional(),
  }),
  theme: ThemeSchema,
  limits: z.object({
    validation: z.object({
      category_min_length: z.number(),
      category_max_length: z.number(),
    }),
  }),
});

export type AppConfig = z.infer<typeof AppConfigSchema>;

export function validateAndNormalizeConfig(rawConfig: unknown): AppConfig {
  try {
    const parsed = AppConfigSchema.parse(rawConfig);
    return parsed;
  } catch (error) {
    if (import.meta.env.DEV && error instanceof z.ZodError) {
      console.error("❌ Invalid application configuration:", error.flatten().fieldErrors);
    }
    throw new Error("Application configuration is invalid and could not be parsed.");
  }
}
