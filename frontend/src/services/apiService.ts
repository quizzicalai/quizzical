// src/services/apiService.ts
import type { ApiError } from '../types/api';
import type { Question, Synopsis } from '../types/quiz';
import type { ResultProfileData } from '../types/result';

// --- Core Utilities ---

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
const IS_DEV = import.meta.env.DEV === true;

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
  initialPayload: Synopsis | Question | null;
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

function withTimeout(signal: AbortSignal | null | undefined, timeoutMs = 30000): AbortSignal {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('Request timed out')), timeoutMs);

  if (signal) {
    if (signal.aborted) {
      clearTimeout(timer);
      controller.abort();
    } else {
      signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
  }

  controller.signal.addEventListener('abort', () => clearTimeout(timer), { once: true });
  return controller.signal;
}

export async function apiFetch<T = any>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { method = 'GET', headers, body, query, signal, timeoutMs } = options;
  const url = `${BASE_URL}${path}${buildQuery(query)}`;
  const finalHeaders = { 'Content-Type': 'application/json', ...headers };
  const effectiveSignal = withTimeout(signal, timeoutMs ?? 15000);

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
      signal.addEventListener('abort', () => { clearTimeout(timer); reject(new Error('Polling canceled')); }, { once: true });
    }
  });
}

// --- Exported API Functions ---

export async function startQuiz(category: string, { signal, timeoutMs }: RequestOptions = {}): Promise<StartQuizResponse> {
  const data = await apiFetch<any>('/quiz/start', {
    method: 'POST',
    body: { category },
    signal,
    timeoutMs: timeoutMs ?? 60000,
  });

  const quizId = data.quiz_id || data.quizId;
  const initialPayload = data.question || data.synopsis || data.current_state || null;

  if (!quizId) {
    throw {
      status: 500,
      code: 'invalid_start_response',
      message: 'Quiz start did not return a quiz id',
      retriable: false,
      details: IS_DEV ? data : undefined,
    } as ApiError;
  }

  return { quizId, initialPayload };
}

export async function getQuizStatus(quizId: string, { knownQuestionsCount, signal, timeoutMs }: RequestOptions & { knownQuestionsCount?: number; } = {}): Promise<QuizStatusDTO> {
  return apiFetch<QuizStatusDTO>(`/quiz/status/${encodeURIComponent(quizId)}`, {
    method: 'GET',
    query: {
      known_questions_count: knownQuestionsCount,
    },
    signal,
    timeoutMs: timeoutMs ?? 15000,
  });
}

interface PollOptions extends RequestOptions {
    knownQuestionsCount?: number;
    totalTimeoutMs?: number;
    initialDelayMs?: number;
    maxIntervalMs?: number;
    onTick?: (status: QuizStatusDTO) => void;
}

export async function pollQuizStatus(quizId: string, {
  knownQuestionsCount = 0,
  totalTimeoutMs = 60000,
  initialDelayMs = 1000,
  maxIntervalMs = 5000,
  signal,
  onTick,
}: PollOptions = {}): Promise<QuizStatusDTO> {
  const start = Date.now();
  let attempt = 0;

  if (initialDelayMs > 0) await sleep(initialDelayMs, signal);

  while (true) {
    const elapsed = Date.now() - start;
    if (elapsed >= totalTimeoutMs) {
      throw { status: 408, code: 'poll_timeout', message: 'Timed out waiting for quiz state', retriable: false } as ApiError;
    }

    let status: QuizStatusDTO | undefined;
    try {
      status = await getQuizStatus(quizId, {
        knownQuestionsCount,
        signal,
        timeoutMs: Math.max(5000, Math.min(10000, totalTimeoutMs - elapsed)),
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
    const nextDelay = Math.min(maxIntervalMs, 1000 + attempt * 500 + Math.random() * 300);
    await sleep(nextDelay, signal);
  }
}

export async function submitAnswer(quizId: string, answerId: string, { signal, timeoutMs }: RequestOptions = {}): Promise<{ status: string }> {
  return apiFetch(`/quiz/${encodeURIComponent(quizId)}/answer`, {
    method: 'POST',
    body: { answer_id: answerId },
    signal,
    timeoutMs: timeoutMs ?? 15000,
  });
}

export async function submitFeedback(quizId: string, { rating, comment }: { rating: 'up' | 'down', comment?: string }, { signal, timeoutMs }: RequestOptions = {}): Promise<void> {
  return apiFetch(`/quiz/${encodeURIComponent(quizId)}/feedback`, {
    method: 'POST',
    body: { rating, comment },
    signal,
    timeoutMs: timeoutMs ?? 10000,
  });
}

export async function getResult(resultId: string, { signal, timeoutMs }: RequestOptions = {}): Promise<ResultProfileData> {
  return apiFetch<ResultProfileData>(`/result/${encodeURIComponent(resultId)}`, {
    method: 'GET',
    signal,
    timeoutMs: timeoutMs ?? 15000,
  });
}