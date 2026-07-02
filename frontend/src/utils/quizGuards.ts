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

  const rawPhrase = raw?.progressPhrase ?? raw?.progress_phrase ?? undefined;
  const progressPhrase =
    typeof rawPhrase === 'string' && rawPhrase.trim() !== '' ? rawPhrase.trim() : undefined;

  const rawNumber = raw?.questionNumber ?? raw?.question_number ?? undefined;
  const questionNumber =
    typeof rawNumber === 'number' && Number.isFinite(rawNumber) && rawNumber > 0
      ? Math.floor(rawNumber)
      : undefined;

  const rawConfidence = raw?.confidence ?? raw?.current_confidence ?? undefined;
  const confidence =
    typeof rawConfidence === 'number' && Number.isFinite(rawConfidence) && rawConfidence > 0
      ? rawConfidence
      : undefined;

  // Deep-review #11 (2026-07-02): the backend Question has no `id`, so this
  // was always `undefined` — silently violating the declared `id: string`
  // contract and killing everything keyed on `question.id` (the focus-move to
  // the new h2 and the per-question entrance animation both never re-fired).
  // Synthesize a stable identity: the served ordinal when present (distinct
  // across consecutive questions, stable across re-serves of the same
  // question), else a text-derived fallback, else a degenerate constant.
  const textStr = String(text ?? '');
  const id: string =
    typeof raw?.id === 'string' && raw.id
      ? raw.id
      : questionNumber != null
        ? `q-${questionNumber}`
        : textStr
          ? `q-${textStr.slice(0, 40)}`
          : 'q-unknown';

  return {
    id,
    text: textStr,
    imageUrl: raw?.imageUrl ?? raw?.image_url ?? undefined,
    imageAlt: raw?.imageAlt ?? undefined,
    answers,
    progressPhrase,
    questionNumber,
    confidence,
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
 *
 * Blended-profile pilot: when the backend marks a result `blended_profile` and
 * carries a `profile`, we pass both through so FinalPage can render the profile
 * view. Anything else (the default) keeps the single-character shape unchanged.
 */
export function toUiResult(raw: any): ResultProfileData {
  const base: ResultProfileData = {
    profileTitle: raw?.title ?? '',
    imageUrl: raw?.imageUrl ?? undefined, // ← null becomes undefined
    imageAlt: raw?.title ?? undefined,
    summary: raw?.description ?? '',
    traits: Array.isArray(raw?.traits) ? raw.traits : undefined,
    shareUrl: raw?.shareUrl ?? undefined,
  };

  // Only attach blended fields when the backend actually sent a blend; a
  // single-character result (no/other resultKind) is returned untouched.
  if (raw?.resultKind === 'blended_profile' && raw?.profile) {
    const p = raw.profile;
    base.resultKind = 'blended_profile';
    base.profile = {
      dimensions: Array.isArray(p?.dimensions)
        ? p.dimensions.map((d: any) => ({
            name: String(d?.name ?? ''),
            emphasis: Number.isFinite(Number(d?.emphasis)) ? Number(d.emphasis) : 0,
            blurb: String(d?.blurb ?? ''),
          }))
        : [],
      primary: String(p?.primary ?? ''),
      secondary: p?.secondary ?? undefined,
      narrative: String(p?.narrative ?? raw?.description ?? ''),
    };
  }

  return base;
}
