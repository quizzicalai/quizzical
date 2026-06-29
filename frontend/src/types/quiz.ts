// src/types/quiz.ts
/**
 * Quiz types shared across the app.
 * Minimal changes:
 * - Adds Character type
 * - Adds optional `characters` on Synopsis so the UI can render them up front
 */

/**
 * The data structure for a single answer option.
 */

import type { FinalResultApi } from './result';

export type Answer = {
  id: string;
  text: string;
  imageUrl?: string;
  imageAlt?: string;
  /**
   * PROTOTYPE (prototype/qa-image-enrichment) — precomputed brand-icon id
   * resolved by the semantic router at pack/build time (NOT at runtime).
   * Optional; rendered only when VITE_PROTO_QA_ICONS is on. Decorative.
   */
  iconId?: string | null;
};

/**
 * The data structure for a single question, including its answers.
 */
export type Question = {
  id: string;
  text: string;
  imageUrl?: string;
  imageAlt?: string;
  /**
   * PROTOTYPE (prototype/qa-image-enrichment) — precomputed brand-icon id for
   * the question stem (semantic router; precomputed). Optional; decorative;
   * rendered only when VITE_PROTO_QA_ICONS is on.
   */
  iconId?: string | null;
  answers: Answer[];
  /**
   * Short status string for the upper-right of the quiz card (e.g.
   * "I'm narrowing in\u2026"). Set by the BE per question. May be empty when
   * the BE could not produce one; the FE should render an empty pill in that
   * case rather than fall back to misleading "% complete" text.
   */
  progressPhrase?: string;
  /**
   * 1-based ordinal of this question. Surfaced by the BE so the card can
   * render "Question 14" without the FE doing any counting (the quiz can
   * end early on confidence, so a denominator like "of 20" would mislead).
   */
  questionNumber?: number;
  /**
   * Agent's current confidence in its best-guess profile, in [0,1].
   * Optional — the BE surfaces this so the FE thinking-row can render
   * "(N% confident)" alongside the progress phrase. When omitted the
   * phrase is shown without a confidence suffix.
   */
  confidence?: number;
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

/** Back-compat alias for older imports. */
export type CharacterProfile = Character;

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

export type QuizStatus =
  | { status: 'processing' | 'pending'; type: 'status' }
  | { status: 'finished'; type: 'result'; data: FinalResultApi };
