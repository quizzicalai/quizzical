// frontend/src/store/quizStore.adaptivePolling.spec.ts
//
// AC-FE-PERF-POLL-1..3 — adaptive polling backoff
// Phase 7 (performance): polling must respect server-driven backoff.
//   * AC-FE-PERF-POLL-1: when the poll request rejects with `retryAfterMs`,
//     the next poll is scheduled at MAX(default_delay, retryAfterMs).
//   * AC-FE-PERF-POLL-2: backoff is bounded — the next poll is scheduled within
//     `MAX_POLL_DELAY_MS` (10_000) regardless of upstream `retryAfterMs`.
//   * AC-FE-PERF-POLL-3: a cancelled poll never schedules a follow-up timer.

import { beforeAll, describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

beforeAll(() => {
  (import.meta as any).env = { ...(import.meta as any).env, DEV: false };
});

const sessionMem = { id: null as string | null, saved: null as any };

vi.mock('../utils/session', () => ({
  getQuizId: vi.fn(() => sessionMem.id),
  saveQuizId: vi.fn((id: string) => { sessionMem.id = id; }),
  clearQuizId: vi.fn(() => { sessionMem.id = null; }),
  saveQuizState: vi.fn((s: any) => { sessionMem.saved = s; }),
  getQuizState: vi.fn(() => sessionMem.saved),
}));

const api = {
  startQuiz: vi.fn(),
  pollQuizStatus: vi.fn(),
  getQuizStatus: vi.fn(),
};
vi.mock('../services/apiService', () => api);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2025-01-01T00:00:00Z'));
  sessionMem.id = 'q-poll';
  sessionMem.saved = null;
  api.startQuiz.mockReset();
  api.pollQuizStatus.mockReset();
  api.getQuizStatus.mockReset();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'debug').mockImplementation(() => {});
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.resetModules();
  vi.restoreAllMocks();
});

const importStore = async () => await import('./quizStore');

function activeStore(useQuizStore: any) {
  useQuizStore.setState({
    status: 'active',
    currentView: 'question',
    viewData: { question_text: 'q', answers: [] },
    quizId: 'q-poll',
    knownQuestionsCount: 1,
    answeredCount: 0,
    totalTarget: 6,
    uiError: null,
    isSubmittingAnswer: false,
    isPolling: false,
    retryCount: 0,
    lastPersistTime: 0,
    sessionRecovered: false,
  });
}

describe('quizStore — adaptive polling (Phase 7)', () => {
  it('AC-FE-PERF-POLL-1: schedules next poll at retryAfterMs when greater than default delay', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    // Server says rate-limited with Retry-After: 5s (5000 ms).
    api.pollQuizStatus
      .mockRejectedValueOnce({
        status: 429,
        code: 'rate_limited',
        message: 'rate limited',
        retriable: true,
        retryAfterMs: 5000,
      })
      .mockResolvedValueOnce({
        status: 'active',
        type: 'question',
        data: { question_text: 'q2', answers: [] },
      });

    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

    await useQuizStore.getState().beginPolling();

    // Find the timer used to re-schedule the next poll. The store also queues
    // a small persist via setTimeout(0); ignore those.
    const rescheduleCalls = setTimeoutSpy.mock.calls
      .map(([, delay]) => delay)
      .filter((d) => typeof d === 'number' && (d as number) >= 1000);

    expect(rescheduleCalls.length).toBeGreaterThan(0);
    expect(Math.max(...(rescheduleCalls as number[]))).toBeGreaterThanOrEqual(5000);
  });

  it('AC-FE-PERF-POLL-2: caps next-poll delay at MAX_POLL_DELAY_MS (10s) regardless of retryAfterMs', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    api.pollQuizStatus.mockRejectedValueOnce({
      status: 429,
      code: 'rate_limited',
      message: 'rate limited',
      retriable: true,
      retryAfterMs: 60_000, // server claims 60s
    });

    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

    await useQuizStore.getState().beginPolling();

    const rescheduleCalls = setTimeoutSpy.mock.calls
      .map(([, delay]) => delay)
      .filter((d) => typeof d === 'number' && (d as number) >= 1000);

    expect(rescheduleCalls.length).toBeGreaterThan(0);
    // Cap is 10_000 ms.
    expect(Math.max(...(rescheduleCalls as number[]))).toBeLessThanOrEqual(10_000);
  });

  it('AC-FE-PERF-POLL-3: a 404 (session expired) does not schedule a follow-up poll', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    api.pollQuizStatus.mockRejectedValueOnce({
      status: 404,
      code: 'not_found',
      message: 'gone',
      retriable: false,
    });

    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

    await useQuizStore.getState().beginPolling();

    const rescheduleCalls = setTimeoutSpy.mock.calls
      .map(([, delay]) => delay)
      .filter((d) => typeof d === 'number' && (d as number) >= 1000);

    expect(rescheduleCalls).toHaveLength(0);
  });
});
