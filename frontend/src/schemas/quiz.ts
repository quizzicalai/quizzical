// src/schemas/quiz.ts
import { z } from 'zod';

/**
 * Mirrors backend/app/models/api.py (camelCase via alias_generator)
 * Keep these lean; FE will still reshape with utils/quizGuards.
 */

export const AnswerOptionSchema = z.object({
  text: z.string(),
  imageUrl: z.string().optional().nullable(),
}).strict();

export const QuestionSchema = z.object({
  text: z.string(),
  imageUrl: z.string().optional().nullable(),
  options: z.array(AnswerOptionSchema),
}).strict();

// Discriminated to match Start payload unions
export const SynopsisSchema = z.object({
  type: z.literal('synopsis'),
  title: z.string(),
  summary: z.string(),
}).strict();

export const CharacterProfileSchema = z.object({
  name: z.string(),
  shortDescription: z.string(),
  profileText: z.string(),
  imageUrl: z.string().optional().nullable(),
}).strict();

export const QuizQuestionSchema = z.object({
  type: z.literal('question'),
  questionText: z.string(),
  // Backend allows array of dicts with at least a 'text' key. Keep permissive.
  options: z.array(z.record(z.string(), z.any())),
}).strict();

// ---- Start Quiz payload wrappers ----
export const StartQuizPayloadSchema = z.discriminatedUnion('type', [
  SynopsisSchema,         // { type: 'synopsis', ... }
  QuizQuestionSchema,     // { type: 'question', questionText, options }
]);

export const CharactersPayloadSchema = z.object({
  type: z.literal('characters'),
  data: z.array(CharacterProfileSchema),
}).strict();

export const FrontendStartQuizResponseSchema = z.object({
  quizId: z.string().min(1),
  initialPayload: z.object({
    type: z.enum(['synopsis', 'question']),
    data: StartQuizPayloadSchema,
  }).optional().nullable(),
  charactersPayload: CharactersPayloadSchema.optional().nullable(),
}).strict();

// ---- Public/DB Result (GET /result/:id) ----
// Backend ShareableResultResponse + a couple of optional fields
export const ShareableResultSchema = z.object({
  title: z.string(),
  description: z.string(),
  imageUrl: z.string().optional().nullable(),
  category: z.string().optional().nullable(),
  createdAt: z.string().optional().nullable(),
}).strict();

// ---- Final result used in status polling ----
export const FinalResultSchema = z.object({
  title: z.string(),
  imageUrl: z.string().optional().nullable(),
  description: z.string(),
}).strict();
