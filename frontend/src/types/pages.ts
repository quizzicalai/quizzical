import type { ContentConfig } from './config';

/**
 * Defines the keys for static pages that can be rendered by the StaticPage component.
 * This ensures that only valid page keys from the config can be used by picking them
 * from the main ContentConfig type.
 */
export type StaticPageKey = keyof Pick<
  ContentConfig,
  'aboutPage' | 'termsPage' | 'privacyPolicyPage'
>;

/**
 * Represents a single block of content for a static page, like a paragraph or a list.
 * This type is defined explicitly to ensure components can correctly handle its shape.
 */
export type StaticContentBlock =
  | { type: 'p'; text: string }
  | { type: 'h2'; text: string }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; items: string[] };

/**
 * Represents a link object, typically used in the footer or other navigation elements.
 */
export interface PageLink {
  href: string;
  label: string;
}
