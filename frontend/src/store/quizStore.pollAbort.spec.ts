/**
 * AC-FE-POLL-PROD-1..3: polling single-flight + abort-on-reset.
 *
 * Verifies that:
 *  - beginPolling installs a fresh AbortController
 *  - reset() aborts any in-flight poll
 *  - a benign abort never surfaces as a user-visible error
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const sessionMem = { id: null as string | null, saved: null as any };
vi.mock('../utils/session', () => ({
  getQuizId: vi.fn(() => sessionMem.id),
  saveQuizId: vi.fn((id: string) => { sessionMem.id = id; }),
  clearQuizId: vi.fn(() => { sessionMem.id = null; sessionMem.saved = null; }),
  saveQuizState: vi.fn((s: any) => { sessionMem.saved = s; }),
  getQuizState: vi.fn(() => sessionMem.saved),
}));

const api = {
  startQuiz: vi.fn(),
  pollQuizStatus: vi.fn(),
  getQuizStatus: vi.fn(),
};
vi.mock('../services/apiService', () => api);

let errorSpy: ReturnType<typeof vi.spyOn>;
let debugSpy: ReturnType<typeof vi.spyOn>;
let warnSpy: ReturnType<typeof vi.spyOn>;
let logSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
  sessionMem.id = null;
  sessionMem.saved = null;
  api.startQuiz.mockReset();
  api.pollQuizStatus.mockReset();
  api.getQuizStatus.mockReset();
});

afterEach(() => {
  errorSpy.mockRestore();
  debugSpy.mockRestore();
  warnSpy.mockRestore();
  logSpy.mockRestore();
  vi.resetModules();
});

const importStore = async () => await import('./quizStore');

function seedActiveSession(useQuizStore: any) {
  useQuizStore.setState({
    quizId: 'quiz-abc',
    status: 'active',
    currentView: 'question',
    viewData: { id: 'q1', text: 'q?', answers: [] },
    knownQuestionsCount: 1,
    answeredCount: 0,
    isPolling: false,
    retryCount: 0,
  });
}

describe('AC-FE-POLL-PROD: poll abort & single-flight', () => {
  it('AC-FE-POLL-PROD-1: beginPolling forwards a signal to api.pollQuizStatus', async () => {
    const { useQuizStore, __getActivePollControllerForTest } = await importStore();
    seedActiveSession(useQuizStore);

    let captured: AbortSignal | undefined;
    api.pollQuizStatus.mockImplementation(async (_id: string, opts: any) => {
      captured = opts?.signal;
      return { status: 'finished', type: 'result', data: {} };
    });

    await useQuizStore.getState().beginPolling();

    expect(api.pollQuizStatus).toHaveBeenCalledTimes(1);
    expect(captured).toBeInstanceOf(AbortSignal);
    expect(captured!.aborted).toBe(false);
    // After a finished snapshot the controller is cleared.
    expect(__getActivePollControllerForTest()).toBeNull();
  });

  it('AC-FE-POLL-PROD-2: reset() aborts the in-flight poll signal', async () => {
    const { useQuizStore, __getActivePollControllerForTest } = await importStore();
    seedActiveSession(useQuizStore);

    let captured: AbortSignal | undefined;
    let release: (v: any) => void = () => {};
    const pending = new Promise((resolve) => { release = resolve; });
    api.pollQuizStatus.mockImplementation(async (_id: string, opts: any) => {
      captured = opts?.signal;
      return await pending;
    });

    const pollPromise = useQuizStore.getState().beginPolling();
    // Let beginPolling install controller + dispatch fetch.
    await Promise.resolve();
    expect(__getActivePollControllerForTest()).not.toBeNull();
    expect(captured).toBeDefined();
    expect(captured!.aborted).toBe(false);

    useQuizStore.getState().reset();

    expect(captured!.aborted).toBe(true);
    expect(__getActivePollControllerForTest()).toBeNull();

    // Release the pending fetch with a canceled error to mimic apiFetch behaviour.
    release({ canceled: true, code: 'canceled', status: 0, retriable: false });
    await pollPromise;

    // Reset cleared the store; uiError must remain null (benign abort).
    expect(useQuizStore.getState().uiError).toBeNull();
    expect(useQuizStore.getState().isPolling).toBe(false);
  });

  it('AC-FE-POLL-PROD-3: a canceled poll never surfaces as a user error', async () => {
    const { useQuizStore } = await importStore();
    seedActiveSession(useQuizStore);

    api.pollQuizStatus.mockImplementation(async () => {
      throw { canceled: true, code: 'canceled', status: 0, retriable: false };
    });

    await useQuizStore.getState().beginPolling();

    expect(useQuizStore.getState().uiError).toBeNull();
    expect(useQuizStore.getState().status).toBe('active');
    expect(useQuizStore.getState().isPolling).toBe(false);
  });
});
