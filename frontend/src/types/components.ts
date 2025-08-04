// src/types/components.ts

/**
 * A generic type for quiz answers, used in multiple components.
 */
export type Answer = {
  id: string;
  text: string;
  imageUrl?: string;
  imageAlt?: string;
};

/**
 * A generic type for quiz questions.
 */
export type Question = {
  id: string;
  text: string;
  imageUrl?: string;
  imageAlt?: string;
  answers: Answer[];
};

/**
 * A generic type for the quiz synopsis.
 */
export type Synopsis = {
    id?: string;
    title: string;
    imageUrl?: string;
    imageAlt?: string;
    summary: string;
};