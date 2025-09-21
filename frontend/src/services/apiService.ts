// src/services/apiService.ts
import type { ApiError } from '../types/api';
import type { Question, Synopsis, CharacterProfile } from '../types/quiz';
import type { ResultProfileData } from '../types/result';
import {
  isRawQuestion,
  isRawSynopsis,
  isWrappedCharacters,
  toUiQuestionFromApi,
  toUiCharacters,
  toUiResult,
} from '../utils/quizGuards';
import type { ApiTimeoutsConfig } from '../types/config';

/* -----------------------------------------------------------------------------
 * Core Utilities
 * ---------------------------------------------------------------------------*/

const API_URL = import.meta.env.VITE_API_URL || ''; // Unset -> ''
const API_BASE_PATH = import.meta.env.VITE_API_BASE_URL || '/api/v1';
const FULL_BASE_URL = `${API_URL}${API_BASE_PATH}`; // -> '/api/v1' (same-origin via nginx)
const IS_DEV = import.meta.env.DEV === true;

// This will be set by the initializeApiService function
let TIMEOUTS: ApiTimeoutsConfig;

/**
 * Initializes the API service with configuration values.
 * This must be called once when the application loads the config.
 * @param timeouts - The timeout configuration object.
 */
export function initializeApiService(timeouts: ApiTimeoutsConfig) {
  TIMEOUTS = timeouts;
}

/* -----------------------------------------------------------------------------
 * Local types (avoid RequestInit entirely)
 * ---------------------------------------------------------------------------*/

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE';

interface QueryParams {
  [key: string]: string | number | boolean | undefined | null;
}

/**
 * Narrow, framework-agnostic fetch options that avoid extending `RequestInit`.
 * This sidesteps ESLint/TS issues when DOM lib or env assumptions differ.
 */
interface ApiFetchOptions {
  method?: HttpMethod;
  headers?: Record<string, string>;
  body?: unknown;
  query?: QueryParams;
  timeoutMs?: number;
  signal?: AbortSignal;
  credentials?: 'omit' | 'same-origin' | 'include';
}

interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

/* -----------------------------------------------------------------------------
 * Payload wrappers used by /quiz/start
 * ---------------------------------------------------------------------------*/

export type WrappedQuestion = { type: 'question'; data: Question };
export type WrappedSynopsis = { type: 'synopsis'; data: Synopsis };
export type WrappedCharacters = { type: 'characters'; data: CharacterProfile[] };

export interface StartQuizResponse {
  quizId: string;
  initialPayload: WrappedQuestion | WrappedSynopsis | null;
  /** NEW: optional characters returned by /quiz/start if they were ready in time */
  charactersPayload?: WrappedCharacters | null;
}

/* -----------------------------------------------------------------------------
 * Status DTO
 * ---------------------------------------------------------------------------*/

export type QuizStatusDTO =
  | { status: 'finished'; type: 'result'; data: ResultProfileData }
  | { status: 'active'; type: 'question'; data: Question }
  | { status: 'processing'; type: 'wait'; quiz_id: string };

/* -----------------------------------------------------------------------------
 * Helper Functions
 * ---------------------------------------------------------------------------*/

function buildQuery(params?: QueryParams): string {
  if (!params) return '';
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

function withTimeout(signal: AbortSignal | null | undefined, timeoutMs: number): AbortSignal {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('Request timed out')), timeoutMs);

  const cleanup = () => clearTimeout(timer);

  if (signal) {
    if (signal.aborted) {
      cleanup();
      controller.abort();
    } else {
      signal.addEventListener(
        'abort',
        () => {
          controller.abort();
          cleanup();
        },
        { once: true }
      );
    }
  }

  controller.signal.addEventListener('abort', cleanup, { once: true });
  return controller.signal;
}

export async function apiFetch<T = unknown>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  // Special handling for the initial config fetch to prevent a circular dependency.
  const isConfigFetch = path === '/config';
  if (!isConfigFetch && !TIMEOUTS) {
    throw new Error('apiService has not been initialized. Call initializeApiService first.');
  }

  const {
    method = 'GET',
    headers,
    body,
    query,
    signal,
    timeoutMs,
    credentials = 'same-origin',
  } = options;

  const url = `${FULL_BASE_URL}${path}${buildQuery(query)}`;
  const finalHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    ...headers,
  };

  // Use a hardcoded timeout for the config fetch, otherwise use the initialized timeouts.
  const effectiveTimeout = isConfigFetch ? 10000 : timeoutMs ?? TIMEOUTS.default;
  const effectiveSignal = withTimeout(signal, effectiveTimeout);

  if (IS_DEV) console.debug(`[api] ${method} ${url}`, { body, query });

  let res: globalThis.Response;
  try {
    res = await fetch(url, {
      method,
      headers: finalHeaders,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: effectiveSignal,
      credentials,
    });
  } catch (err: any) {
    const normalized: ApiError = {
      status: 0,
      code: 'network_error',
      message: err?.name === 'AbortError' ? 'Request canceled or timed out' : 'Network error',
      retriable: err?.name !== 'AbortError',
      details: IS_DEV ? String(err) : undefined,
    };
    if (IS_DEV) console.error('[api] fetch failed', normalized);
    throw normalized;
  }

  const isJson = res.headers.get('content-type')?.includes('application/json');
  const payload = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    const normalized: ApiError = {
      status: res.status,
      code: (payload && (payload.code || payload.error)) || 'http_error',
      message: (payload && (payload.message || payload.detail)) || `HTTP ${res.status}`,
      retriable: res.status >= 500,
      details: IS_DEV ? payload : undefined,
    };
    if (IS_DEV) console.error('[api] non-2xx', normalized);
    throw normalized;
  }

  return payload as T;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener(
        'abort',
        () => {
          clearTimeout(timer);
          reject(new Error('Polling canceled'));
        },
        { once: true }
      );
    }
  });
}

/* -----------------------------------------------------------------------------
 * Small normalizers (minimal, safe)
 * ---------------------------------------------------------------------------*/

function normalizeStatus(raw: any): QuizStatusDTO {
  if (raw && raw.status === 'processing') {
    // Backend returns { status: 'processing', quiz_id } (no type)
    return {
      status: 'processing',
      type: 'wait',
      quiz_id: raw.quiz_id ?? raw.quizId ?? '',
    };
  }

  // Normalize active question (options -> answers)
  if (raw && raw.status === 'active' && raw.type === 'question' && raw.data) {
    return {
      status: 'active',
      type: 'question',
      data: toUiQuestionFromApi(raw.data) as Question,
    };
  }

  // Normalize finished result into UI ResultProfileData
  if (raw && raw.status === 'finished' && raw.type === 'result' && raw.data) {
    return {
      status: 'finished',
      type: 'result',
      data: toUiResult(raw.data),
    };
  }

  // For anything else, pass through (shapes already match)
  return raw as QuizStatusDTO;
}

function mapResultToProfile(raw: any): ResultProfileData {
  // Minimal mapping to align server → client without changing other code paths.
  // (Kept for getResult; uses the same mapping as toUiResult.)
  return toUiResult(raw);
}

/* -----------------------------------------------------------------------------
 * Exported API Functions
 * ---------------------------------------------------------------------------*/

export async function startQuiz(
  category: string,
  turnstileToken: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<StartQuizResponse> {
  const data = await apiFetch<any>('/quiz/start', {
    method: 'POST',
    body: {
      category,
      'cf-turnstile-response': turnstileToken,
    },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.startQuiz,
  });

  const quizId = data.quiz_id || data.quizId;

  if (!quizId) {
    throw {
      status: 500,
      code: 'invalid_start_response',
      message: 'Quiz start did not return a quiz id',
      retriable: false,
      details: IS_DEV ? data : undefined,
    } as ApiError;
  }

  // Honor backend's contract first
  const initial = data.initial_payload ?? data.initialPayload ?? null;
  const characters = data.characters_payload ?? data.charactersPayload ?? null;

  // Normalize initial payload's question data (options → answers)
  let normalizedInitial: WrappedQuestion | WrappedSynopsis | null = null;
  if (initial && initial.type && initial.data) {
  if (initial.type === 'question' && isRawQuestion(initial.data)) {
    normalizedInitial = { type: 'question', data: toUiQuestionFromApi(initial.data) as Question };
  } else if (initial.type === 'synopsis' && isRawSynopsis(initial.data)) {
    normalizedInitial = initial as WrappedSynopsis;
  } else {
    if (IS_DEV) console.error('[startQuiz] Invalid initial payload', initial);
  }
}

  // Normalize characters payload to camelCase fields
  let normalizedCharacters: WrappedCharacters | null = null;
  if (characters && isWrappedCharacters(characters)) {
    normalizedCharacters = {
      type: 'characters',
      data: toUiCharacters(characters.data) as CharacterProfile[],
    };
  } else if (characters && Array.isArray(characters?.data)) {
    // Be tolerant if backend forgot the discriminator
    normalizedCharacters = {
      type: 'characters',
      data: toUiCharacters(characters.data) as CharacterProfile[],
    };
  }

  if (normalizedInitial) {
    return {
      quizId,
      initialPayload: normalizedInitial,
      charactersPayload: normalizedCharacters,
    };
  }

  // Legacy fallbacks (kept intact, but normalize where possible)
  const body = data.question || data.synopsis || data.current_state || null;

  if (!body) {
    return { quizId, initialPayload: null, charactersPayload: normalizedCharacters };
  }

  if ('type' in body && (body as any).data) {
    if ((body as any).type === 'question') {
      return {
        quizId,
        initialPayload: { type: 'question', data: toUiQuestionFromApi((body as any).data) as Question },
        charactersPayload: normalizedCharacters,
      };
    }
    return {
      quizId,
      initialPayload: body as WrappedQuestion | WrappedSynopsis,
      charactersPayload: normalizedCharacters,
    };
  }
  if (isRawQuestion(body)) {
    return {
      quizId,
      initialPayload: { type: 'question', data: body },
      charactersPayload: normalizedCharacters,
    };
  }
  // Tolerate legacy server question shape with `options`
  if (body && typeof body === 'object' && Array.isArray((body as any).options)) {
    return {
      quizId,
      initialPayload: { type: 'question', data: toUiQuestionFromApi(body) as Question },
      charactersPayload: normalizedCharacters,
    };
  }
  if (isRawSynopsis(body)) {
    return {
      quizId,
      initialPayload: { type: 'synopsis', data: body },
      charactersPayload: normalizedCharacters,
    };
  }

  if (IS_DEV) console.warn('[startQuiz] Unknown payload shape', body);
  return { quizId, initialPayload: null, charactersPayload: normalizedCharacters };
}

/**
 * NEW: explicitly advance from synopsis/characters to baseline question generation.
 * Backend route: POST /quiz/proceed
 */
export async function proceedQuiz(
  quizId: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<{ status: 'processing'; quiz_id: string } | { status: 'processing'; quizId: string }> {
  return apiFetch('/quiz/proceed', {
    method: 'POST',
    body: { quizId }, // Pydantic aliasing accepts camelCase
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
}

export async function getQuizStatus(
  quizId: string,
  { knownQuestionsCount, signal, timeoutMs }: RequestOptions & { knownQuestionsCount?: number } = {}
): Promise<QuizStatusDTO> {
  const raw = await apiFetch<any>(`/quiz/status/${encodeURIComponent(quizId)}`, {
    method: 'GET',
    query: {
      known_questions_count: knownQuestionsCount,
    },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
  return normalizeStatus(raw);
}

interface PollOptions extends RequestOptions {
  knownQuestionsCount?: number;
  onTick?: (status: QuizStatusDTO) => void;
}

export async function pollQuizStatus(
  quizId: string,
  {
    knownQuestionsCount = 0,
    signal,
    onTick,
  }: PollOptions = {}
): Promise<QuizStatusDTO> {
  const { total, interval, maxInterval } = TIMEOUTS.poll;
  const start = Date.now();
  let attempt = 0;

  if (interval > 0) await sleep(interval, signal);

  while (true) {
    const elapsed = Date.now() - start;
    if (elapsed >= total) {
      throw { status: 408, code: 'poll_timeout', message: 'Timed out waiting for quiz state', retriable: false } as ApiError;
    }

    let status: QuizStatusDTO | undefined;
    try {
      status = await getQuizStatus(quizId, {
        knownQuestionsCount,
        signal,
        timeoutMs: Math.max(5000, Math.min(10000, total - elapsed)),
      });
    } catch (err: any) {
      if (!err?.retriable) throw err;
      if (IS_DEV) console.warn('[api] poll retriable error', err);
    }

    if (status) {
      onTick?.(status);
      if (status.status === 'finished' || (status.status === 'active' && status.type === 'question')) {
        return status;
      }
    }

    attempt += 1;
    const nextDelay = Math.min(maxInterval, interval + attempt * 500 + Math.random() * 300);
    await sleep(nextDelay, signal);
  }
}

export async function submitAnswer(
  quizId: string,
  answer: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<{ status: string }> {
  return apiFetch('/quiz/next', {
    method: 'POST',
    body: { quizId, answer }, // backend accepts camelCase via Pydantic alias generator
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
}

export async function submitFeedback(
  quizId: string,
  { rating, comment }: { rating: 'up' | 'down'; comment?: string },
  turnstileToken: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<void> {
  return apiFetch('/feedback', {
    method: 'POST',
    body: {
      quiz_id: quizId, // explicit server shape
      rating,
      text: comment,
      'cf-turnstile-response': turnstileToken,
    },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
}

export async function getResult(resultId: string, { signal, timeoutMs }: RequestOptions = {}): Promise<ResultProfileData> {
  const raw = await apiFetch<any>(`/result/${encodeURIComponent(resultId)}`, {
    method: 'GET',
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
  return mapResultToProfile(raw);
}
