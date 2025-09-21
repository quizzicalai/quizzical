// src/utils/quizGuards.ts
import type { Answer, Question as UIQuestion, Synopsis, CharacterProfile } from '../types/quiz';
import type { ResultProfileData } from '../types/result';

/* -----------------------------------------------------------------------------
 * Discriminated wrappers used by /quiz/start
 * ---------------------------------------------------------------------------*/

export type WrappedSynopsis = { type: 'synopsis'; data: Synopsis };
export type WrappedQuestion = { type: 'question'; data: UIQuestion };
export type WrappedCharacters = { type: 'characters'; data: CharacterProfile[] };

export type InitialPayload =
  | WrappedSynopsis
  | WrappedQuestion
  | Synopsis
  | UIQuestion
  | null
  | undefined;

/* -----------------------------------------------------------------------------
 * Guards (kept lightweight & tolerant)
 * ---------------------------------------------------------------------------*/

export function isWrappedSynopsis(p: unknown): p is WrappedSynopsis {
  return !!p && typeof p === 'object'
    && (p as any).type === 'synopsis'
    && isRawSynopsis((p as any).data);
}
export function isWrappedQuestion(p: unknown): p is WrappedQuestion {
  return !!p && typeof p === 'object'
    && (p as any).type === 'question'
    && isRawQuestion((p as any).data);
}

export function isWrappedCharacters(p: unknown): p is WrappedCharacters {
  const ok =
    !!p &&
    typeof p === 'object' &&
    (p as any).type === 'characters' &&
    Array.isArray((p as any).data);
  return ok;
}

/** UI question shape = must have an `answers` array */
export function isRawQuestion(p: unknown): p is UIQuestion {
  return !!p && typeof p === 'object' && Array.isArray((p as any).answers);
}

export function isRawSynopsis(p: unknown): p is Synopsis {
  const obj = p as any;
  return (
    !!obj &&
    typeof obj === 'object' &&
    !Array.isArray(obj.answers) &&
    typeof obj.summary === 'string' &&
    typeof obj.title === 'string'
  );
}

/* -----------------------------------------------------------------------------
 * API → UI Normalizers
 *  - Accepts tolerant inputs (snake_case, older shapes, or partials)
 *  - Produces the UI types used across the app
 * ---------------------------------------------------------------------------*/

/**
 * Normalize a list of options (API: `options`) into UI `Answer[]`.
 * Supports strings or dicts with { text, imageUrl|image_url }.
 */
export function toUiAnswers(
  options: Array<{ text?: string; label?: string; imageUrl?: string; image_url?: string } | string | unknown>
): Answer[] {
  const list = Array.isArray(options) ? options : [];
  return list.map((opt, idx) => {
    const text =
      typeof opt === 'string'
        ? opt
        : (opt as any)?.text ?? (opt as any)?.label ?? String(opt ?? '');

    const imageUrl =
      typeof opt === 'string'
        ? undefined
        : (opt as any)?.imageUrl ?? (opt as any)?.image_url ?? undefined;

    const safeText = String(text ?? '').trim();

    return {
      id: `opt-${idx}`,
      text: safeText,
      imageUrl,
      imageAlt: safeText || undefined,
    };
  });
}

/**
 * Normalize an API Question (backend `Question` or legacy `QuizQuestion`) into the UI `Question`.
 * - Accepts `text` or legacy `question_text`
 * - Converts `options` → `answers`
 * - Carries through optional `image_url|imageUrl`
 */
export function toUiQuestionFromApi(raw: any): UIQuestion {
  const text =
    raw?.text ?? raw?.question_text ?? raw?.questionText ?? '';

  const options =
    (Array.isArray(raw?.options) ? raw.options : []) as Array<any>;

  const answers = toUiAnswers(options);

  return {
    id: raw?.id ?? undefined,
    text: String(text ?? ''),
    imageUrl: raw?.imageUrl ?? raw?.image_url ?? undefined,
    imageAlt: raw?.imageAlt ?? undefined,
    answers,
  };
}

/**
 * Normalize API characters (snake_case) to UI `CharacterProfile[]` (camelCase).
 */
export function toUiCharacters(raw: any[]): CharacterProfile[] {
  const arr = Array.isArray(raw) ? raw : [];
  return arr.map((c) => ({
    name: c?.name ?? '',
    shortDescription: c?.shortDescription ?? c?.short_description ?? '',
    profileText: c?.profileText ?? c?.profile_text ?? '',
    imageUrl: c?.imageUrl ?? c?.image_url ?? undefined,
  }));
}

/**
 * Normalize API FinalResult into UI `ResultProfileData`.
 */
export function toUiResult(raw: any): ResultProfileData {
  return {
    profileTitle: raw?.profileTitle ?? raw?.title ?? '',
    summary: raw?.summary ?? raw?.description ?? '',
    imageUrl: raw?.imageUrl ?? raw?.image_url ?? undefined,
    shareUrl: raw?.shareUrl ?? raw?.share_url ?? undefined,
  };
}
