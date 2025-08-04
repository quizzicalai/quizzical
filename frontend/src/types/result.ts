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
};