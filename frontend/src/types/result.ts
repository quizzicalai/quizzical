// src/types/result.ts

/**
 * Represents a single trait in the user's profile.
 */
export type Trait = {
  id?: string;
  label: string;
  value?: string;
};

/**
 * Discriminates how a result should be rendered. Defaults to single-character
 * everywhere (older snapshots omit it); a gated pilot blended topic (DISC) sets
 * 'blended_profile' so the FE swaps in the profile view.
 */
export type ResultKind = 'single_character' | 'blended_profile';

/**
 * One canonical dimension within a blended-profile result (e.g. DISC's
 * Dominance/Influence/Steadiness/Conscientiousness).
 */
export type BlendedDimension = {
  name: string;
  /** Relative emphasis 0–100 — an emphasis signal for the bars, not a score. */
  emphasis: number;
  blurb: string;
};

/**
 * Profile/blend payload — present only when resultKind === 'blended_profile'.
 */
export type BlendedProfile = {
  dimensions: BlendedDimension[];
  primary: string;
  secondary?: string | null;
  narrative: string;
};

/**
 * The complete data structure for a user's quiz result profile.
 */
export type ResultProfileData = {
  id?: string;
  profileTitle: string;
  imageUrl?: string;
  imageAlt?: string;
  summary: string;
  traits?: Trait[];
  shareUrl?: string;
  /** Additive; absent → single-character (the existing behaviour). */
  resultKind?: ResultKind;
  /** Additive; present only for blended_profile results. */
  profile?: BlendedProfile;
};

/**
 * Backend "final result" payload shape (matches logs exactly).
 * We keep this separate from UI types to avoid churn.
 */
export type FinalResultApi = {
  title: string;
  imageUrl: string | null; // backend can return null
  description: string;
  resultKind?: ResultKind;
  profile?: BlendedProfile;
};