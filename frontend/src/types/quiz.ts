// src/types/quiz.ts

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
 * The data structure for the initial quiz synopsis.
 */
export type Synopsis = {
  id?: string;
  title: string;
  imageUrl?: string;
  imageAlt?: string;
  summary: string;
};