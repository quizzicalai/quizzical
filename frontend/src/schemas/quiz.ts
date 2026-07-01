// src/schemas/quiz.ts
import { z } from 'zod';

/**
 * Mirrors backend/app/models/api.py (camelCase via alias_generator)
 * Keep these lean; FE will still reshape with utils/quizGuards.
 */

/* -----------------------------------------------------------------------------
 * Question & Answer option payloads
 * ---------------------------------------------------------------------------*/

export const AnswerOptionSchema = z.object({
  text: z.string(),
  imageUrl: z.string().optional().nullable(),
}).strict();

export const QuestionSchema = z.object({
  text: z.string(),
  imageUrl: z.string().optional().nullable(),
  options: z.array(AnswerOptionSchema),
  progressPhrase: z.string().optional().nullable(),
  questionNumber: z.number().int().positive().optional().nullable(),
  // AC-UX-2026-05-08 — optional 0–1 confidence surfaced by the agent.
  confidence: z.number().min(0).max(1).optional().nullable(),
}).strict();

/* -----------------------------------------------------------------------------
 * Start Quiz payloads (discriminated)
 * ---------------------------------------------------------------------------*/

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

/**
 * Backend question variant for Start payload
 * (UI normalization converts options -> answers, etc.)
 */
export const QuizQuestionSchema = z.object({
  type: z.literal('question'),
  questionText: z.string(),
  // Backend allows array of dicts with at least a 'text' key. Keep permissive.
  options: z.array(z.record(z.string(), z.any())),
}).strict();

export const StartQuizPayloadSchema = z.discriminatedUnion('type', [
  SynopsisSchema,     // { type: 'synopsis', ... }
  QuizQuestionSchema, // { type: 'question', questionText, options }
]);

export const CharactersPayloadSchema = z.object({
  type: z.literal('characters'),
  data: z.array(CharacterProfileSchema),
}).strict();

/**
 * Frontend start response (camelCase). The FE expects a wrapper:
 *   initialPayload: { type: 'synopsis'|'question', data: <StartQuizPayload> }
 */
export const FrontendStartQuizResponseSchema = z.object({
  quizId: z.string().min(1),
  initialPayload: z.object({
    type: z.enum(['synopsis', 'question']),
    data: StartQuizPayloadSchema,
  }).optional().nullable(),
  charactersPayload: CharactersPayloadSchema.optional().nullable(),
}).strict();

/* -----------------------------------------------------------------------------
 * Result payloads
 *  - Allow optional traits + shareUrl to align with UI tolerance
 *  - Keep strict() so unknown keys still fail (other than the ones we allow)
 * ---------------------------------------------------------------------------*/

export const TraitSchema = z.object({
  id: z.union([z.string(), z.number()]).optional(),
  label: z.string(),
  value: z.string().optional().nullable(),
}).strict();

/**
 * Blended-profile payload (gated DISC pilot). Additive + optional everywhere so
 * single-character results — which omit it entirely — keep validating. The
 * outer result schemas stay `.strict()`, so these keys MUST be declared here or
 * a blended payload would be rejected as a schema_error.
 */
export const BlendedDimensionSchema = z.object({
  name: z.string(),
  emphasis: z.number(),
  blurb: z.string(),
}).strict();

export const BlendedProfileSchema = z.object({
  dimensions: z.array(BlendedDimensionSchema),
  primary: z.string(),
  secondary: z.string().optional().nullable(),
  narrative: z.string(),
}).strict();

/**
 * Public/DB result (GET /result/:id)
 * Backend ShareableResultResponse + optional fields used by the UI.
 */
export const ShareableResultSchema = z.object({
  title: z.string(),
  description: z.string(),
  imageUrl: z.string().optional().nullable(),
  // Optional extras tolerated by the UI:
  traits: z.array(TraitSchema).optional().nullable(),
  shareUrl: z.string().optional().nullable(),
  // Blended-profile pilot (additive; absent on single-character results):
  resultKind: z.enum(['single_character', 'blended_profile']).optional().nullable(),
  profile: BlendedProfileSchema.optional().nullable(),
  // Existing optional metadata:
  category: z.string().optional().nullable(),
  createdAt: z.string().optional().nullable(),
}).strict();

/**
 * Final result used inside status polling:
 *   { status: 'finished', type: 'result', data: FinalResultSchema }
 */
export const FinalResultSchema = z.object({
  title: z.string(),
  description: z.string(),
  imageUrl: z.string().optional().nullable(),
  // New optional fields:
  traits: z.array(TraitSchema).optional().nullable(),
  shareUrl: z.string().optional().nullable(),
  // Blended-profile pilot (additive; absent on single-character results):
  resultKind: z.enum(['single_character', 'blended_profile']).optional().nullable(),
  profile: BlendedProfileSchema.optional().nullable(),
}).strict();
