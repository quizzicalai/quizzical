// src/schemas/status.ts
//
// NOTE: these object schemas intentionally STRIP (do not .strict()) unknown
// top-level keys. The backend agent evolves and may add fields to status/start
// responses; a `.strict()` schema would throw on every poll for any additive
// field, and that ZodError (no `status`/`retriable`) was misclassified by the
// store as a transient network error — burning retries then dead-ending the
// user on harmless deploy skew. Stripping keeps forward-compatibility while
// still validating the known fields.
import { z } from 'zod';
import { QuestionSchema, FinalResultSchema } from './quiz';

export const ProcessingResponseSchema = z.object({
  status: z.literal('processing'),
  // Backend can send quizId (camel) per aliasing; keep both just in case.
  quizId: z.string().optional(),
  quiz_id: z.string().optional(),
});

export const QuizStatusQuestionSchema = z.object({
  status: z.literal('active'),
  type: z.literal('question'),
  data: z.record(z.string(), z.any()).or(QuestionSchema),
});

export const QuizStatusResultSchema = z.object({
  status: z.literal('finished'),
  type: z.literal('result'),
  data: FinalResultSchema,
});

export const QuizStatusResponseSchema = z.union([
  ProcessingResponseSchema,
  QuizStatusQuestionSchema,
  QuizStatusResultSchema,
]);
