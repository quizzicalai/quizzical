// frontend/src/services/apiService.ts

/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import type { ApiError } from '../types/api';
import type { Question, Synopsis, CharacterProfile } from '../types/quiz';
import type { ResultProfileData } from '../types/result';
import type { ApiTimeoutsConfig } from '../types/config';

import {
  toUiQuestionFromApi,
  toUiCharacters,
  toUiResult,
  isRawQuestion,
  isRawSynopsis,
  isWrappedCharacters,
} from '../utils/quizGuards';

import {
  FrontendStartQuizResponseSchema,
  QuizStatusResponseSchema,
  ShareableResultSchema,
} from '../schemas';

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE';

interface QueryParams {
  [key: string]: string | number | boolean | undefined | null;
}

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
 * Constants / env
 * ---------------------------------------------------------------------------*/
const IS_DEV = import.meta.env.DEV === true;
const USE_DB_RESULTS = (import.meta.env.VITE_USE_DB_RESULTS ?? 'false') === 'true';

// Support both configurations cleanly:
//
// 1) Absolute VITE_API_BASE_URL (recommended in Azure), e.g.:
//    VITE_API_BASE_URL=https://api-quizzical-dev...azurecontainerapps.io/api/v1
//
// 2) Pair of VITE_API_URL (origin) + VITE_API_BASE_URL (path), e.g.:
//    VITE_API_URL=http://localhost:8000
//    VITE_API_BASE_URL=/api/v1
//
// If neither is provided in dev, default to http://localhost:8000/api/v1.
const RAW_API_URL = (import.meta.env.VITE_API_URL as string | undefined) || '';
const RAW_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api/v1';

function stripTrailingSlash(s: string): string {
  return s.endsWith('/') ? s.slice(0, -1) : s;
}

function ensureLeadingSlash(s: string): string {
  if (!s) return '/';
  return s.startsWith('/') ? s : `/${s}`;
}

function isAbsoluteUrl(s: string): boolean {
  return /^https?:\/\//i.test(s);
}

function resolveBaseUrl(): string {
  // Absolute base provided → use as-is (minus trailing slash)
  if (RAW_BASE && isAbsoluteUrl(RAW_BASE)) {
    return stripTrailingSlash(RAW_BASE);
  }

  // Otherwise compose origin + path
  const origin = RAW_API_URL
    ? stripTrailingSlash(RAW_API_URL)
    : IS_DEV
      ? 'http://localhost:8000'
      : '';

  const path = ensureLeadingSlash(RAW_BASE || '/api/v1');

  if (!origin && !IS_DEV) {
    throw new Error(
      'VITE_API configuration missing. In production, set either an absolute VITE_API_BASE_URL or VITE_API_URL + VITE_API_BASE_URL.',
    );
  }

  return `${origin}${stripTrailingSlash(path)}`;
}

const BASE_URL = resolveBaseUrl();

let TIMEOUTS: ApiTimeoutsConfig | undefined;

export function initializeApiService(timeouts: ApiTimeoutsConfig) {
  TIMEOUTS = timeouts;
}

/* -----------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------------*/
function buildQuery(params?: QueryParams): string {
  if (!params) return '';
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue;
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

function joinUrl(base: string, path: string): string {
  const b = stripTrailingSlash(base);
  return path.startsWith('/') ? `${b}${path}` : `${b}/${path}`;
}

/**
 * Generate a per-request correlation id (X-Request-Id). Uses crypto.randomUUID
 * when available (all modern browsers + secure contexts) and falls back to a
 * RFC4122-shaped string assembled from `crypto.getRandomValues` for older
 * runtimes / non-secure contexts. The output always matches the BE-side
 * regex `^[A-Za-z0-9_.\-]{1,128}$` so it is honored verbatim.
 */
export function generateRequestId(): string {
  try {
    const c: any = (globalThis as any).crypto;
    if (c?.randomUUID) return c.randomUUID();
    if (c?.getRandomValues) {
      const buf = new Uint8Array(16);
      c.getRandomValues(buf);
      // Per RFC4122 §4.4
      buf[6] = (buf[6] & 0x0f) | 0x40;
      buf[8] = (buf[8] & 0x3f) | 0x80;
      const hex = Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
  } catch {
    /* fall through */
  }
  // Last-resort fallback (still BE-regex safe).
  return `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Core fetch wrapper used by all API calls.
 * - Allows `/config` before initializeApiService is called.
 * - Treats aborts as benign and throws a normalized `{ canceled: true }` error.
 */
export async function apiFetch<T = unknown>(path: string, options: ApiFetchOptions = {}): Promise<T> {
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

  const url = `${joinUrl(BASE_URL, path)}${buildQuery(query)}`;
  // AC-FE-OBS-REQID-1: every outbound request carries an X-Request-Id so the
  // BE can echo it back as `X-Trace-ID`/`X-Request-ID` and operators can
  // correlate FE actions with BE logs.
  const requestId = generateRequestId();
  const finalHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Request-Id': requestId,
    ...headers,
  };

  const effectiveTimeout = isConfigFetch ? 10_000 : (timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).default);
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
    // Normalize aborts as benign "canceled"
    if (err?.name === 'AbortError') {
      const canceled = {
        status: 0,
        code: 'canceled',
        message: 'Request was aborted',
        retriable: false,
        canceled: true,
      } as const;
      if (IS_DEV) console.debug('[api] fetch aborted (benign)', canceled);
      // Throw so callers can detect and ignore gracefully.
      throw canceled as unknown as ApiError;
    }

    const normalized: ApiError = {
      status: 0,
      code: 'network_error',
      message: 'Network error',
      retriable: true,
      details: IS_DEV ? String(err) : undefined,
    };
    if (IS_DEV) console.error('[api] fetch failed', normalized);
    throw normalized;
  }

  const isJson = res.headers.get('content-type')?.includes('application/json');
  const payload = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    const normalized = normalizeHttpError(res, payload);
    // AC-FE-OBS-REQID-2: surface the BE-echoed trace id on errors so it can
    // appear in user-facing diagnostics and help operators correlate logs.
    const traceId =
      res.headers.get('X-Trace-Id') ||
      res.headers.get('X-Request-Id') ||
      requestId;
    if (traceId) (normalized as ApiError).traceId = traceId;
    if (IS_DEV) console.error('[api] non-2xx', normalized);
    throw normalized;
  }

  return payload as T;
}

/**
 * Normalize an HTTP error response into the canonical ApiError shape.
 *
 * Handles backend-defined ``errorCode`` values (RATE_LIMITED, SESSION_BUSY,
 * PAYLOAD_TOO_LARGE) and parses ``Retry-After`` for 429 responses.
 * Exported for unit testing (FE-ERR-PROD-1..5).
 */
export function normalizeHttpError(
  res: { status: number; headers: { get(name: string): string | null } },
  payload: any,
): ApiError {
  const beErrorCode: string | undefined = payload && (payload.errorCode || payload.error_code) || undefined;
  const beMessage: string | undefined = payload && (payload.message || payload.detail) || undefined;
  const beCode: string | undefined = payload && (payload.code || payload.error) || undefined;

  // Default mapping: 5xx is retriable.
  const err: ApiError = {
    status: res.status,
    code: beCode || 'http_error',
    errorCode: beErrorCode,
    message: beMessage || `HTTP ${res.status}`,
    retriable: res.status >= 500,
    details: IS_DEV ? payload : undefined,
  };

  // 429 — RATE_LIMITED (FE-ERR-PROD-1).
  if (res.status === 429) {
    const ra = res.headers.get('Retry-After');
    let retryMs = 1000;
    if (ra) {
      const asInt = parseInt(ra, 10);
      if (Number.isFinite(asInt) && asInt > 0) retryMs = asInt * 1000;
    }
    err.code = 'rate_limited';
    err.errorCode = err.errorCode || 'RATE_LIMITED';
    err.retriable = true;
    err.retryAfterMs = retryMs;
    return err;
  }

  // 409 — SESSION_BUSY (FE-ERR-PROD-2).
  if (res.status === 409 && beErrorCode === 'SESSION_BUSY') {
    err.code = 'session_busy';
    err.retriable = false;
    return err;
  }

  // 413 — PAYLOAD_TOO_LARGE (FE-ERR-PROD-3).
  if (res.status === 413) {
    err.code = 'payload_too_large';
    err.errorCode = err.errorCode || 'PAYLOAD_TOO_LARGE';
    err.message = 'Your input is too long.';
    err.retriable = false;
    return err;
  }

  // 422 — Pydantic validation (FE-ERR-PROD-4).
  if (res.status === 422) {
    err.code = 'validation_error';
    err.retriable = false;
    return err;
  }

  // 502 — Bad Gateway (FE-ERR-PROD-8). BE upstream/proxy could not be reached.
  if (res.status === 502) {
    err.code = 'bad_gateway';
    err.message = 'The server could not reach an upstream service. Please try again.';
    err.retriable = true;
    return err;
  }

  // 503 — Service Unavailable (FE-ERR-PROD-6).
  if (res.status === 503) {
    err.code = 'service_unavailable';
    err.message = 'The server is temporarily busy. Please try again in a moment.';
    err.retriable = true;
    return err;
  }

  // 504 — Gateway Timeout (FE-ERR-PROD-7).
  if (res.status === 504) {
    err.code = 'gateway_timeout';
    err.message = 'The request timed out. Please try again.';
    err.retriable = true;
    return err;
  }

  // §9.7.5 AC-FE-ERR-PROD-1..4 — friendly fallback for any unenumerated
  // 4xx / 5xx response. Without this, users would see raw BE `detail`
  // strings (often technical/implementation-leaking) for any new error
  // path the FE hasn't been taught about. We deliberately overwrite
  // `message` so a stack-trace dump or DB error message never reaches
  // the UI. The original payload is still attached as `details` in dev.
  if (res.status >= 500) {
    err.code = err.code === 'http_error' ? 'server_error' : err.code;
    err.message = 'Something went wrong on our end. Please try again.';
    err.retriable = true;
    return err;
  }
  if (res.status >= 400) {
    err.code = err.code === 'http_error' ? 'client_error' : err.code;
    err.message =
      'Something went wrong with your request. Please refresh and try again.';
    err.retriable = false;
    return err;
  }

  return err;
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
 * Public DTOs & wrappers
 * ---------------------------------------------------------------------------*/
export type WrappedQuestion = { type: 'question'; data: Question };
export type WrappedSynopsis = { type: 'synopsis'; data: Synopsis };
export type WrappedCharacters = { type: 'characters'; data: CharacterProfile[] };

export interface StartQuizResponse {
  quizId: string;
  initialPayload: WrappedQuestion | WrappedSynopsis | null;
  charactersPayload?: WrappedCharacters | null;
}

export type QuizStatusDTO =
  | { status: 'finished'; type: 'result'; data: ResultProfileData }
  | { status: 'active'; type: 'question'; data: Question }
  | { status: 'processing'; type: 'wait'; quiz_id: string };

/* -----------------------------------------------------------------------------
 * Normalizers
 * ---------------------------------------------------------------------------*/
function normalizeStatus(raw: any): QuizStatusDTO {
  if (raw && raw.status === 'processing') {
    return {
      status: 'processing',
      type: 'wait',
      quiz_id: raw.quiz_id ?? raw.quizId ?? '',
    };
  }

  if (raw && raw.status === 'active' && raw.type === 'question' && raw.data) {
    return {
      status: 'active',
      type: 'question',
      data: toUiQuestionFromApi(raw.data) as Question,
    };
  }

  if (raw && raw.status === 'finished' && raw.type === 'result' && raw.data) {
    return {
      status: 'finished',
      type: 'result',
      data: toUiResult(raw.data),
    };
  }

  return raw as QuizStatusDTO;
}

function mapResultToProfile(raw: any): ResultProfileData {
  return toUiResult(raw);
}

/* -----------------------------------------------------------------------------
 * API
 * ---------------------------------------------------------------------------*/

export async function startQuiz(
  category: string,
  turnstileToken: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<StartQuizResponse> {
  const unvalidated = await apiFetch<any>('/quiz/start', {
    method: 'POST',
    body: { category, 'cf-turnstile-response': turnstileToken },
    signal,
    timeoutMs: timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).startQuiz,
  });

  // Validate the *shape* using Zod; backend sends camelCase
  // Accept legacy top-level keys (quiz_id) and coerce to camel for FE.
  const camel = {
    quizId: unvalidated.quizId ?? unvalidated.quiz_id,
    initialPayload: unvalidated.initialPayload ?? unvalidated.initial_payload,
    charactersPayload: unvalidated.charactersPayload ?? unvalidated.characters_payload,
  };

  const parsed = FrontendStartQuizResponseSchema.parse(camel);
  const quizId = parsed.quizId;

  // Normalize initial payload for the UI
  let normalizedInitial: WrappedQuestion | WrappedSynopsis | null = null;
  const initial = parsed.initialPayload;

  if (initial && initial.type === 'question') {
    const q = toUiQuestionFromApi(initial.data) as Question;
    if (q && isRawQuestion(q)) {
      normalizedInitial = { type: 'question', data: q };
    } else if (IS_DEV) {
      console.error('[startQuiz] Invalid question payload', initial.data);
    }
  } else if (initial && initial.type === 'synopsis') {
    const d: any = initial.data;
    const syn: Synopsis = { title: d?.title ?? '', summary: d?.summary ?? '' };
    if (isRawSynopsis(syn)) {
      normalizedInitial = { type: 'synopsis', data: syn };
    } else if (IS_DEV) {
      console.error('[startQuiz] Invalid synopsis payload', initial.data);
    }
  }

  // Normalize characters payload
  let normalizedCharacters: WrappedCharacters | null = null;
  const characters = parsed.charactersPayload;
  if (characters && isWrappedCharacters(characters)) {
    normalizedCharacters = { type: 'characters', data: toUiCharacters(characters.data) as CharacterProfile[] };
  } else if (characters && Array.isArray(characters?.data)) {
    normalizedCharacters = { type: 'characters', data: toUiCharacters(characters.data) as CharacterProfile[] };
  }

  // Fallbacks for legacy fields if needed (rare after schema validation)
  if (!normalizedInitial) {
    const legacy = unvalidated.question || unvalidated.synopsis || unvalidated.current_state || null;
    if (legacy) {
      if (legacy?.type === 'question' && legacy?.data) {
        normalizedInitial = { type: 'question', data: toUiQuestionFromApi(legacy.data) as Question };
      } else if (isRawQuestion(legacy)) {
        normalizedInitial = { type: 'question', data: legacy };
      } else if (Array.isArray(legacy?.options)) {
        normalizedInitial = { type: 'question', data: toUiQuestionFromApi(legacy) as Question };
      } else if (isRawSynopsis(legacy)) {
        normalizedInitial = { type: 'synopsis', data: legacy };
      }
    }
  }

  return {
    quizId,
    initialPayload: normalizedInitial,
    charactersPayload: normalizedCharacters,
  };
}

export async function proceedQuiz(
  quizId: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<{ status: 'processing'; quiz_id: string } | { status: 'processing'; quizId: string }> {
  return apiFetch('/quiz/proceed', {
    method: 'POST',
    body: { quizId },
    signal,
    timeoutMs: timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).default,
  });
}

export async function getQuizStatus(
  quizId: string,
  { knownQuestionsCount, signal, timeoutMs }: RequestOptions & { knownQuestionsCount?: number } = {}
): Promise<QuizStatusDTO> {
  const raw = await apiFetch<any>(`/quiz/status/${encodeURIComponent(quizId)}`, {
    method: 'GET',
    query: { known_questions_count: knownQuestionsCount },
    signal,
    timeoutMs: timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).default,
  });

  // Validate then normalize
  const parsed = QuizStatusResponseSchema.parse(raw);
  return normalizeStatus(parsed);
}

interface PollOptions extends RequestOptions {
  knownQuestionsCount?: number;
  onTick?: (status: QuizStatusDTO) => void;
}

export async function pollQuizStatus(
  quizId: string,
  { knownQuestionsCount = 0, signal, onTick }: PollOptions = {}
): Promise<QuizStatusDTO> {
  const { total, interval, maxInterval } = (TIMEOUTS as ApiTimeoutsConfig).poll;
  const start = Date.now();
  let attempt = 0;

  if (interval > 0) await sleep(interval, signal);

  while (true) {
    const elapsed = Date.now() - start;
    if (elapsed >= total) {
      throw { status: 408, code: 'poll_timeout', message: 'Timed out waiting for quiz state', retriable: false } as ApiError;
    }

    let status: QuizStatusDTO | undefined;
    let retryAfterOverrideMs = 0;
    try {
      status = await getQuizStatus(quizId, {
        knownQuestionsCount,
        signal,
        timeoutMs: Math.max(5_000, Math.min(10_000, total - elapsed)),
      });
    } catch (err: any) {
      if (!err?.retriable) throw err;
      if (typeof err?.retryAfterMs === 'number' && err.retryAfterMs > 0) {
        retryAfterOverrideMs = err.retryAfterMs;
      }
      if (IS_DEV) console.warn('[api] poll retriable error', err);
    }

    if (status) {
      onTick?.(status);
      if (status.status === 'finished' || (status.status === 'active' && status.type === 'question')) {
        return status;
      }
    }

    attempt += 1;
    const backoff = Math.min(maxInterval, interval + attempt * 500 + Math.random() * 300);
    // FE-ERR-PROD-5: honour Retry-After when the BE rate-limits us.
    const nextDelay = Math.max(backoff, retryAfterOverrideMs);
    await sleep(nextDelay, signal);
  }
}

export async function submitAnswer(
  quizId: string,
  params: { questionIndex: number; optionIndex?: number; answer?: string },
  { signal, timeoutMs }: RequestOptions = {}
): Promise<{ status: string }> {
  const { questionIndex, optionIndex, answer } = params;
  return apiFetch('/quiz/next', {
    method: 'POST',
    body: { quizId, questionIndex, optionIndex, answer },
    signal,
    timeoutMs: timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).default,
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
      quiz_id: quizId,
      rating,
      text: comment,
      'cf-turnstile-response': turnstileToken,
    },
    signal,
    timeoutMs: timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).default,
  });
}

export async function getResult(
  resultId: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<ResultProfileData> {
  const tryStatus = async (): Promise<ResultProfileData | null> => {
    const status = await getQuizStatus(resultId, { signal, timeoutMs });
    if (status.status === 'finished' && status.type === 'result') {
      return status.data;
    }
    return null;
  };

  const tryDb = async (): Promise<ResultProfileData> => {
    const raw = await apiFetch<any>(`/result/${encodeURIComponent(resultId)}`, {
      method: 'GET',
      signal,
      timeoutMs: timeoutMs ?? (TIMEOUTS as ApiTimeoutsConfig).default,
    });
    // Validate DB payload; then map to UI model
    const parsed = ShareableResultSchema.parse(raw);
    return mapResultToProfile(parsed);
  };

  if (!USE_DB_RESULTS) {
    const fromStatus = await tryStatus();
    if (fromStatus) return fromStatus;
    throw { status: 404, code: 'not_found', message: 'Result not found in cache', retriable: false } as ApiError;
  }

  try {
    return await tryDb();
  } catch (err: any) {
    if (err?.status === 404 || err?.status === 403) {
      const fromStatus = await tryStatus();
      if (fromStatus) return fromStatus;
    }
    throw err;
  }
}
