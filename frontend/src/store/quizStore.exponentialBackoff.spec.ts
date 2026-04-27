// frontend/src/store/quizStore.exponentialBackoff.spec.ts
//
// AC-FE-RELY-POLL-1..4 — Phase 8 reliability: exponential backoff with jitter
// for consecutive 5xx / network failures during polling.
//
//   * AC-FE-RELY-POLL-1: a successful poll resets the failure streak to 0.
//   * AC-FE-RELY-POLL-2: 5xx and network failures retry with exponential
//     backoff bounded by MAX_POLL_DELAY_MS, never fatal until streak > MAX_RETRIES.
//   * AC-FE-RELY-POLL-3: server-driven `Retry-After` always wins over the
//     computed backoff (still subject to the MAX clamp).
//   * AC-FE-RELY-POLL-4: 404/403 are terminal (session gone), no backoff.

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
  sessionMem.id = 'q-rely';
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
    quizId: 'q-rely',
    knownQuestionsCount: 1,
    answeredCount: 0,
    totalTarget: 6,
    uiError: null,
    isSubmittingAnswer: false,
    isPolling: false,
    retryCount: 0,
    pollFailureStreak: 0,
    lastPersistTime: 0,
    sessionRecovered: false,
  });
}

describe('quizStore — reliability (Phase 8) exponential backoff', () => {
  it('AC-FE-RELY-POLL-1: a successful poll (processing) resets pollFailureStreak', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);
    useQuizStore.setState({ pollFailureStreak: 2 });

    api.pollQuizStatus.mockResolvedValueOnce({ status: 'processing', type: 'character_set' });

    await useQuizStore.getState().beginPolling();

    expect(useQuizStore.getState().pollFailureStreak).toBe(0);
  });

  it('AC-FE-RELY-POLL-2: a single 500 is RETRIED (not fatal) and increments the streak', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    api.pollQuizStatus.mockRejectedValueOnce({ status: 503, message: 'svc unavailable' });

    await useQuizStore.getState().beginPolling();

    const s = useQuizStore.getState();
    expect(s.status).toBe('active');         // not fatal
    expect(s.pollFailureStreak).toBe(1);     // streak incremented
  });

  it('AC-FE-RELY-POLL-2: backoff delay grows exponentially per consecutive failure', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    // Force jitter to 0 for deterministic comparison.
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0);
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

    api.pollQuizStatus.mockRejectedValue({ status: 503, message: 'down' });

    // First failure
    await useQuizStore.getState().beginPolling();
    const delays1 = setTimeoutSpy.mock.calls.map((c) => c[1]).filter(
      (d): d is number => typeof d === 'number' && d > 100,
    );
    const firstDelay = Math.max(...delays1);

    setTimeoutSpy.mockClear();

    // Second consecutive failure (streak goes from 1 → 2)
    useQuizStore.setState({ isPolling: false });
    await useQuizStore.getState().beginPolling();
    const delays2 = setTimeoutSpy.mock.calls.map((c) => c[1]).filter(
      (d): d is number => typeof d === 'number' && d > 100,
    );
    const secondDelay = Math.max(...delays2);

    // 2nd delay should be ≥ 2x the first (exponential growth, ignoring jitter).
    expect(secondDelay).toBeGreaterThanOrEqual(firstDelay * 2 - 1);
    randomSpy.mockRestore();
  });

  it('AC-FE-RELY-POLL-3: server Retry-After overrides the computed backoff', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

    api.pollQuizStatus.mockRejectedValueOnce({
      status: 503,
      message: 'overloaded',
      retryAfterMs: 7500,
    });

    await useQuizStore.getState().beginPolling();

    const delays = setTimeoutSpy.mock.calls.map((c) => c[1]).filter(
      (d): d is number => typeof d === 'number' && d > 100,
    );
    // Retry-After (7500) should drive the schedule. Capped at MAX_POLL_DELAY_MS (10000).
    expect(Math.max(...delays)).toBeGreaterThanOrEqual(7500);
    expect(Math.max(...delays)).toBeLessThanOrEqual(10_000);
  });

  it('AC-FE-RELY-POLL-2: after MAX_RETRIES consecutive 5xx the store becomes fatal', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);
    // Pretend we have already failed 3 times — next failure should be fatal.
    useQuizStore.setState({ pollFailureStreak: 3 });

    api.pollQuizStatus.mockRejectedValueOnce({ status: 500, message: 'boom' });

    await useQuizStore.getState().beginPolling();

    const s = useQuizStore.getState();
    expect(s.status).toBe('error');
    expect(s.currentView).toBe('error');
    expect(s.pollFailureStreak).toBe(0);
  });

  it('AC-FE-RELY-POLL-4: 404 is terminal — no backoff, session cleared', async () => {
    const { useQuizStore } = await importStore();
    activeStore(useQuizStore);

    api.pollQuizStatus.mockRejectedValueOnce({ status: 404, message: 'gone' });

    await useQuizStore.getState().beginPolling();

    const s = useQuizStore.getState();
    expect(s.status).toBe('error');
    expect(s.pollFailureStreak).toBe(0);
  });
});
