// src/services/apiService.js

// --- Core Utilities ---

const BASE_URL = import.meta.env.VITE_BFF_BASE_URL || '/api';
const IS_DEV = import.meta.env.DEV === true;

/**
 * Builds a URL query string from an object.
 * @param {object} params - The parameters to encode.
 * @returns {string} The URL query string.
 */
function buildQuery(params) {
  if (!params) return '';
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

/**
 * Creates a new AbortSignal that aborts when the original signal aborts or when the timeout is reached.
 * @param {AbortSignal} signal - The original AbortSignal.
 * @param {number} timeoutMs - The timeout in milliseconds.
 * @returns {AbortSignal} The new AbortSignal.
 */
function withTimeout(signal, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('Request timed out')), timeoutMs);

  if (signal) {
    if (signal.aborted) {
      controller.abort();
    } else {
      signal.addEventListener('abort', () => controller.abort(), { once: true });
    }
  }

  controller.signal.addEventListener('abort', () => clearTimeout(timer), { once: true });
  return controller.signal;
}

/**
 * A centralized and robust fetch function for all API requests.
 * It handles error normalization, logging, and timeouts.
 * @param {string} path - The API endpoint path.
 * @param {object} options - Fetch options.
 * @returns {Promise<any>} The response payload.
 */
async function apiFetch(path, {
  method = 'GET',
  headers,
  body,
  query,
  signal,
  timeoutMs,
} = {}) {
  const url = `${BASE_URL}${path}${buildQuery(query)}`;
  const finalHeaders = { 'Content-Type': 'application/json', ...(headers || {}) };
  const effectiveSignal = withTimeout(signal, timeoutMs ?? 15000);

  if (IS_DEV) console.debug(`[api] ${method} ${url}`, { body, query });

  let res;
  try {
    res = await fetch(url, {
      method,
      headers: finalHeaders,
      body: body ? JSON.stringify(body) : undefined,
      signal: effectiveSignal,
      credentials: 'same-origin',
    });
  } catch (err) {
    const normalized = {
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
  const payload = isJson ? (await res.json().catch(() => null)) : await res.text();

  if (!res.ok) {
    const normalized = {
      status: res.status,
      code: (payload && (payload.code || payload.error)) || 'http_error',
      message: (payload && (payload.message || payload.detail)) || `HTTP ${res.status}`,
      retriable: res.status >= 500,
      details: IS_DEV ? payload : undefined,
    };
    if (IS_DEV) console.error('[api] non-2xx', normalized);
    throw normalized;
  }

  return payload;
}

/**
 * A simple sleep utility for polling delays.
 * @param {number} ms - The number of milliseconds to sleep.
 * @param {AbortSignal} signal - An AbortSignal to cancel the sleep.
 * @returns {Promise<void>}
 */
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener('abort', () => { clearTimeout(t); reject(new Error('Polling canceled')); }, { once: true });
    }
  });
}

// --- Exported API Functions ---

/**
 * Starts a new quiz and returns the quiz ID and initial payload (e.g., synopsis).
 * @param {string} category - The category for the new quiz.
 * @param {object} opts - Optional parameters like signal and timeoutMs.
 * @returns {Promise<{quizId: string, initialPayload: object}>}
 */
export async function startQuiz(category, { signal, timeoutMs } = {}) {
  const data = await apiFetch('/quiz/start', {
    method: 'POST',
    body: { category },
    signal,
    timeoutMs: timeoutMs ?? 15000,
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
    };
  }

  return { quizId, initialPayload };
}

/**
 * Fetches the current status of a quiz.
 * @param {string} quizId - The ID of the quiz.
 * @param {object} opts - Optional parameters like knownQuestionsCount, signal, and timeoutMs.
 * @returns {Promise<object>} The current quiz status.
 */
export async function getQuizStatus(quizId, {
  knownQuestionsCount,
  signal,
  timeoutMs,
} = {}) {
  return apiFetch(`/quiz/status/${encodeURIComponent(quizId)}`, {
    method: 'GET',
    query: {
      known_questions_count: Number.isFinite(knownQuestionsCount) ? knownQuestionsCount : undefined,
    },
    signal,
    timeoutMs: timeoutMs ?? 15000,
  });
}

/**
 * Polls the quiz status endpoint until a meaningful state change occurs or it times out.
 * @param {string} quizId - The ID of the quiz.
 * @param {object} opts - Polling options.
 * @returns {Promise<object>} The final status object.
 */
export async function pollQuizStatus(quizId, {
  knownQuestionsCount = 0,
  totalTimeoutMs = 60000,
  initialDelayMs = 1000,
  maxIntervalMs = 5000,
  signal,
  onTick, // optional callback(status)
} = {}) {
  const start = Date.now();
  let attempt = 0;

  if (initialDelayMs) await sleep(initialDelayMs, signal);

  while (true) {
    const elapsed = Date.now() - start;
    if (elapsed >= totalTimeoutMs) {
      throw { status: 408, code: 'poll_timeout', message: 'Timed out waiting for quiz state', retriable: false };
    }

    let status;
    try {
      status = await getQuizStatus(quizId, {
        knownQuestionsCount,
        signal,
        timeoutMs: Math.max(5000, Math.min(10000, totalTimeoutMs - elapsed)),
      });
    } catch (err) {
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

/**
 * Submits an answer for a given quiz.
 * @param {string} quizId - The ID of the quiz.
 * @param {string} answerId - The ID of the selected answer.
 * @param {object} opts - Optional parameters like signal and timeoutMs.
 * @returns {Promise<object>} The acknowledgment response.
 */
export async function submitAnswer(quizId, answerId, { signal, timeoutMs } = {}) {
  return apiFetch(`/quiz/${encodeURIComponent(quizId)}/answer`, {
    method: 'POST',
    body: { answer_id: answerId },
    signal,
    timeoutMs: timeoutMs ?? 15000,
  });
}

/**
 * Submits feedback for a completed quiz.
 * @param {string} quizId - The ID of the quiz.
 * @param {object} feedback - The feedback object { rating, comment }.
 * @param {object} opts - Optional parameters like signal and timeoutMs.
 * @returns {Promise<object>} The acknowledgment response.
 */
export async function submitFeedback(quizId, { rating, comment }, { signal, timeoutMs } = {}) {
  return apiFetch(`/quiz/${encodeURIComponent(quizId)}/feedback`, {
    method: 'POST',
    body: { rating, comment },
    signal,
    timeoutMs: timeoutMs ?? 10000,
  });
}

/**
 * Retrieves the shareable result for a completed quiz.
 * @param {string} resultId - The ID of the result/session.
 * @param {object} opts - Optional parameters like signal and timeoutMs.
 * @returns {Promise<object>} The result payload.
 */
export async function getResult(resultId, { signal, timeoutMs } = {}) {
  return apiFetch(`/result/${encodeURIComponent(resultId)}`, {
    method: 'GET',
    signal,
    timeoutMs: timeoutMs ?? 15000,
  });
}

/**
 * A lightweight helper to retry a function that may fail due to transient errors.
 * @param {Function} taskFn - The async function to retry.
 * @param {object} opts - Retry options.
 * @returns {Promise<any>} The result of the task function.
 */
export async function withRetry(taskFn, {
  retries = 2,
  baseDelayMs = 400,
  maxDelayMs = 1500,
  signal,
  retriable = (err) => Boolean(err?.retriable),
} = {}) {
  let attempt = 0;
  while (true) {
    try {
      return await taskFn();
    } catch (err) {
      if (attempt >= retries || !retriable(err)) throw err;
      attempt += 1;
      const delay = Math.min(maxDelayMs, baseDelayMs * attempt + Math.random() * 250);
      await sleep(delay, signal);
    }
  }
}