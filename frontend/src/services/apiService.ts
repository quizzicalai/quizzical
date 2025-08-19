// src/services/apiService.ts
import type { ApiError } from '../types/api';
import type { Question, Synopsis } from '../types/quiz';
import type { ResultProfileData } from '../types/result';
import type { ApiTimeoutsConfig } from '../types/config';
import { isRawQuestion, isRawSynopsis, WrappedQuestion, WrappedSynopsis } from '../utils/quizGuards';

// --- Core Utilities ---

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
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

// --- Type Definitions ---

interface QueryParams {
  [key: string]: string | number | boolean | undefined | null;
}

interface ApiFetchOptions extends RequestInit {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  headers?: Record<string, string>;
  body?: any;
  query?: QueryParams;
  timeoutMs?: number;
}

interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

interface StartQuizResponse {
  quizId: string;
  initialPayload: WrappedQuestion | WrappedSynopsis | null;
}

export type QuizStatusDTO =
  | { status: 'finished'; type: 'result'; data: ResultProfileData }
  | { status: 'active'; type: 'question'; data: Question }
  | { status: 'processing'; type: 'wait'; quiz_id: string };

// --- Helper Functions ---

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
      signal.addEventListener('abort', () => {
        controller.abort();
        cleanup();
      }, { once: true });
    }
  }

  controller.signal.addEventListener('abort', cleanup, { once: true });
  return controller.signal;
}

export async function apiFetch<T = any>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  if (!TIMEOUTS) {
    throw new Error('apiService has not been initialized. Call initializeApiService first.');
  }
  const { method = 'GET', headers, body, query, signal, timeoutMs } = options;
  const url = `${BASE_URL}${path}${buildQuery(query)}`;
  const finalHeaders = { 'Content-Type': 'application/json', ...headers };
  const effectiveSignal = withTimeout(signal, timeoutMs ?? TIMEOUTS.default);

  if (IS_DEV) console.debug(`[api] ${method} ${url}`, { body, query });

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: finalHeaders,
      body: body ? JSON.stringify(body) : undefined,
      signal: effectiveSignal,
      credentials: 'same-origin',
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

// --- Exported API Functions ---

export async function startQuiz(
  category: string,
  turnstileToken: string,
  { signal, timeoutMs }: RequestOptions = {}
): Promise<StartQuizResponse> {
  const data = await apiFetch<any>('/quiz/start', {
    method: 'POST',
    body: {
      category,
      'cf-turnstile-response': turnstileToken
    },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.startQuiz,
  });

  const quizId = data.quiz_id || data.quizId;
  const body = data.question || data.synopsis || data.current_state || null;

  if (!quizId) {
    throw {
      status: 500,
      code: 'invalid_start_response',
      message: 'Quiz start did not return a quiz id',
      retriable: false,
      details: IS_DEV ? data : undefined,
    } as ApiError;
  }

  if (!body) {
    return { quizId, initialPayload: null };
  }

  if ('type' in body && body.data) {
    return { quizId, initialPayload: body as WrappedQuestion | WrappedSynopsis };
  }
  if (isRawQuestion(body)) {
    return { quizId, initialPayload: { type: 'question', data: body } };
  }
  if (isRawSynopsis(body)) {
    return { quizId, initialPayload: { type: 'synopsis', data: body } };
  }

  if (import.meta.env.DEV) console.warn('[startQuiz] Unknown payload shape', body);
  return { quizId, initialPayload: null };
}

export async function getQuizStatus(
  quizId: string,
  { knownQuestionsCount, signal, timeoutMs }: RequestOptions & { knownQuestionsCount?: number } = {}
): Promise<QuizStatusDTO> {
  return apiFetch<QuizStatusDTO>(`/quiz/status/${encodeURIComponent(quizId)}`, {
    method: 'GET',
    query: {
      known_questions_count: knownQuestionsCount,
    },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
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
  answer: string, // Changed from answerId to answer to reflect payload key
  { signal, timeoutMs }: RequestOptions = {}
): Promise<{ status: string }> {
  // CORRECTED: Endpoint is /quiz/next, not /quiz/{id}/answer
  return apiFetch('/quiz/next', {
    method: 'POST',
    // CORRECTED: Payload requires quizId and the answer text
    body: { quizId, answer },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
}

export async function submitFeedback(
  quizId: string,
  { rating, comment }: { rating: 'up' | 'down'; comment?: string },
  turnstileToken: string, // Added turnstileToken parameter
  { signal, timeoutMs }: RequestOptions = {}
): Promise<void> {
  // CORRECTED: Endpoint is /quiz/feedback, with quizId in the body
  return apiFetch('/quiz/feedback', {
    method: 'POST',
    // CORRECTED: Payload requires quizId and the Turnstile token
    body: {
      quizId,
      rating,
      comment,
      'cf-turnstile-response': turnstileToken,
    },
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
}

export async function getResult(resultId: string, { signal, timeoutMs }: RequestOptions = {}): Promise<ResultProfileData> {
  return apiFetch<ResultProfileData>(`/result/${encodeURIComponent(resultId)}`, {
    method: 'GET',
    signal,
    timeoutMs: timeoutMs ?? TIMEOUTS.default,
  });
}