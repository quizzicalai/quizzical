// src/config/configSchema.js
import { z } from 'zod';

// Schema for a single link in the footer
const FooterLinkSchema = z.object({
  label: z.string().min(1),
  href: z.string().min(1),
  external: z.boolean().optional(),
});

// Schema for the entire footer configuration
const FooterConfigSchema = z.object({
  about: FooterLinkSchema,
  terms: FooterLinkSchema,
  privacy: FooterLinkSchema,
  donate: FooterLinkSchema,
});

// Schema for different types of content blocks on static pages
const StaticBlockSchema = z.union([
  z.object({ type: z.literal('p'), text: z.string() }),
  z.object({ type: z.literal('h2'), text: z.string() }),
  z.object({ type: z.literal('ul'), items: z.array(z.string()) }),
  z.object({ type: z.literal('ol'), items: z.array(z.string()) }),
]);

// Schema for a complete static page (e.g., About, Terms)
const StaticPageSchema = z.object({
  title: z.string(),
  blocks: z.array(StaticBlockSchema),
});

// Schema for all labels and text on the result page
const ResultPageConfigSchema = z.object({
  titlePrefix: z.string().optional(),
  shareButton: z.string(),
  shareCopied: z.string(),
  startOverButton: z.string(),
  traitListTitle: z.string().optional(),
  feedback: z.object({
    prompt: z.string(),
    thumbsUp: z.string(),
    thumbsDown: z.string(),
    commentPlaceholder: z.string(),
    submit: z.string(),
    thanks: z.string(),
  }),
  share: z.object({
    socialTitle: z.string(),
    socialDescription: z.string(),
  }),
});

// Schema for all user-facing error messages
const ErrorsConfigSchema = z.object({
  title: z.string(),
  description: z.string(),
  retry: z.string(),
  home: z.string(),
  startOver: z.string(),
  categoryNotFound: z.string(),
  requestTimeout: z.string(),
  quizCreationFailed: z.string(),
  resultNotFound: z.string(),
});

// The complete schema for the `content` part of the configuration
export const ContentConfigSchema = z.object({
  footer: FooterConfigSchema,
  aboutPage: StaticPageSchema,
  termsPage: StaticPageSchema,
  privacyPolicyPage: StaticPageSchema,
  resultPage: ResultPageConfigSchema,
  errors: ErrorsConfigSchema,
  // NOTE: You can add other top-level content sections here (e.g., landingPage)
});

/**
 * Validates the content part of the configuration object.
 * Throws an error if the configuration is invalid.
 * @param {object} contentConfig - The content configuration object to validate.
 * @returns {object} The validated configuration object.
 */
export function validateContentConfig(contentConfig) {
  try {
    return ContentConfigSchema.parse(contentConfig);
  } catch (error) {
    console.error("Configuration validation failed:", error.errors);
    throw new Error("Invalid application content configuration.");
  }
}