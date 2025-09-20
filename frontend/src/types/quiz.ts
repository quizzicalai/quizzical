/**
 * Quiz types shared across the app.
 * Minimal changes:
 * - Adds Character type
 * - Adds optional `characters` on Synopsis so the UI can render them up front
 */

/**
 * The data structure for a single answer option.
 */
export type Answer = {
  id: string;
  text: string;
  imageUrl?: string;
  imageAlt?: string;
};

/**
 * The data structure for a single question, including its answers.
 */
export type Question = {
  id: string;
  text: string;
  imageUrl?: string;
  imageAlt?: string;
  answers: Answer[];
};

/**
 * The data structure for a generated character profile (lightweight for UI).
 */
export type Character = {
  name: string;
  shortDescription: string;
  profileText: string;
  imageUrl?: string;
};

/**
 * The data structure for the initial quiz synopsis.
 * NOTE: `characters` is optional; backend may attach these when available.
 */
export type Synopsis = {
  id?: string;
  title: string;
  imageUrl?: string;
  imageAlt?: string;
  summary: string;
  characters?: Character[];
};
