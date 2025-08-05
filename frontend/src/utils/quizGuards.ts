import type { Question, Synopsis } from '../types/quiz';

export type WrappedSynopsis = { type: 'synopsis'; data: Synopsis };
export type WrappedQuestion = { type: 'question'; data: Question };
export type InitialPayload = WrappedSynopsis | WrappedQuestion | Synopsis | Question | null | undefined;

export function isWrappedSynopsis(p: unknown): p is WrappedSynopsis {
  return !!p && typeof p === 'object' && (p as any).type === 'synopsis' && !!(p as any).data;
}

export function isWrappedQuestion(p: unknown): p is WrappedQuestion {
  return !!p && typeof p === 'object' && (p as any).type === 'question' && !!(p as any).data;
}

export function isRawQuestion(p: unknown): p is Question {
  return !!p && typeof p === 'object' && Array.isArray((p as any).answers);
}

export function isRawSynopsis(p: unknown): p is Synopsis {
  const obj = p as any;
  return !!obj && typeof obj === 'object' &&
         !Array.isArray(obj.answers) &&
         typeof obj.summary === 'string' &&
         typeof obj.title === 'string';
}