// src/schemas/status.ts
import { z } from 'zod';
import { QuestionSchema, FinalResultSchema } from './quiz';

export const ProcessingResponseSchema = z.object({
  status: z.literal('processing'),
  // Backend can send quizId (camel) per aliasing; keep both just in case.
  quizId: z.string().optional(),
  quiz_id: z.string().optional(),
}).strict();

export const QuizStatusQuestionSchema = z.object({
  status: z.literal('active'),
  type: z.literal('question'),
  data: z.record(z.string(), z.any()).or(QuestionSchema),
}).strict();

export const QuizStatusResultSchema = z.object({
  status: z.literal('finished'),
  type: z.literal('result'),
  data: FinalResultSchema,
}).strict();

export const QuizStatusResponseSchema = z.union([
  ProcessingResponseSchema,
  QuizStatusQuestionSchema,
  QuizStatusResultSchema,
]);
