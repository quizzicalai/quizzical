// frontend/src/store/quizStore.spec.ts
import { beforeAll, describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// --- Ensure DEV=true globally so DEV-only logging branches are exercised (override per-test as needed)
beforeAll(() => {
  (import.meta as any).env = { ...(import.meta as any).env, DEV: true };
});

// --- In-memory session "storage" for mocks
const sessionMem = {
  id: null as string | null,
  saved: null as any,
};

// --- Mock session utils
vi.mock('../utils/session', () => {
  return {
    getQuizId: vi.fn(() => sessionMem.id),
    saveQuizId: vi.fn((id: string) => { sessionMem.id = id; }),
    clearQuizId: vi.fn(() => { sessionMem.id = null; sessionMem.saved = null; }),
    saveQuizState: vi.fn((s: any) => { sessionMem.saved = s; }),
    getQuizState: vi.fn(() => sessionMem.saved),
  };
});

// --- Mock API service
const api = {
  startQuiz: vi.fn(),
  pollQuizStatus: vi.fn(),
  getQuizStatus: vi.fn(),
};
vi.mock('../services/apiService', () => api);

// --- Console spies (silence noise, but we still assert calls where relevant)
let logSpy: ReturnType<typeof vi.spyOn>;
let errorSpy: ReturnType<typeof vi.spyOn>;
let warnSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2025-01-01T00:00:00Z'));
  logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  sessionMem.id = null;
  sessionMem.saved = null;
  api.startQuiz.mockReset();
  api.pollQuizStatus.mockReset();
  api.getQuizStatus.mockReset();
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  logSpy.mockRestore();
  errorSpy.mockRestore();
  warnSpy.mockRestore();
  vi.resetModules();
});

// --- Import store AFTER mocks/DEV are ready
const importStore = async () => await import('./quizStore');

// --- Helper: reset Zustand store to baseline without touching action refs
const resetStore = (useQuizStore: any) => {
  useQuizStore.setState({
    status: 'idle',
    currentView: 'idle',
    viewData: null,
    quizId: null,
    knownQuestionsCount: 0,
    answeredCount: 0,
    totalTarget: 20,
    uiError: null,
    isSubmittingAnswer: false,
    isPolling: false,
    retryCount: 0,
    lastPersistTime: 0,
    sessionRecovered: false,
  });
};

// --- Small helpers to manipulate the DEV flag temporarily
const getDEV = () => (import.meta as any).env?.DEV;
const setDEV = (v: boolean) => { (import.meta as any).env = { ...(import.meta as any).env, DEV: v }; };

describe('quizStore.ts', () => {
  it('startQuiz: success (wrapped synopsis + characters)', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    api.startQuiz.mockResolvedValue({
      quizId: 'q-1',
      initialPayload: { type: 'synopsis', data: { title: 'T', summary: 'S' } },
      charactersPayload: {
        type: 'characters',
        data: [{ name: 'Ada', short_description: 'sd', profile_text: 'pt', image_url: 'img' }],
      },
    });

    await useQuizStore.getState().startQuiz('Ancient Rome', 'tok');
    const s = useQuizStore.getState();

    expect(s.status).toBe('active');
    expect(s.currentView).toBe('synopsis');
    expect(s.viewData).toMatchObject({
      title: 'T',
      summary: 'S',
      characters: [{ name: 'Ada', shortDescription: 'sd', profileText: 'pt', imageUrl: 'img' }],
    });
    expect(s.knownQuestionsCount).toBe(0);
    expect(s.answeredCount).toBe(0);
  });

  it('startQuiz: success (wrapped question uses UI shape as-is)', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    api.startQuiz.mockResolvedValue({
      quizId: 'q-2',
      initialPayload: { type: 'question', data: { id: '1', text: 'Q', answers: [] } },
      charactersPayload: null,
    });

    await useQuizStore.getState().startQuiz('Cat', 'tok');

    const s = useQuizStore.getState();
    expect(s.status).toBe('active');
    expect(s.currentView).toBe('question');
    expect(s.viewData).toMatchObject({ id: '1', text: 'Q', answers: [] });
    expect(s.knownQuestionsCount).toBe(1);
  });

  it('startQuiz: failure surfaces ApiError message and sets fatal error state', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    api.startQuiz.mockRejectedValue({ message: 'oops' });

    await expect(useQuizStore.getState().startQuiz('Cat', 'tok')).rejects.toBeTruthy();

    const s = useQuizStore.getState();
    expect(s.status).toBe('error');
    expect(s.currentView).toBe('error');
    expect(s.uiError).toBe('oops');
    expect(errorSpy).toHaveBeenCalled(); // logged in DEV
  });

  it('hydrateFromStart: handles raw synopsis and warns on invalid characters payload', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.getState().hydrateFromStart({
      quizId: 'q-3',
      initialPayload: { title: 'A', summary: 'B' }, // raw synopsis
      charactersPayload: { type: 'characters', data: 'not-an-array' as any },
    });

    const s = useQuizStore.getState();
    expect(s.quizId).toBe('q-3');
    expect(s.status).toBe('active');
    expect(s.currentView).toBe('synopsis');
    expect(s.viewData).toMatchObject({ title: 'A', summary: 'B' });
    expect(warnSpy).toHaveBeenCalled(); // DEV-only warning for bad characters payload
  });

  it('hydrateFromStart: invalid payload leaves view idle but activates store (logs in DEV)', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    const localErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    useQuizStore.getState().hydrateFromStart({
      quizId: 'q-4',
      initialPayload: null,
      charactersPayload: null,
    });

    const s = useQuizStore.getState();
    expect(s.quizId).toBe('q-4');
    expect(s.status).toBe('active');
    expect(s.currentView).toBe('idle'); // nothing matched
    expect(s.viewData).toBeNull();

    // Since Vite inlines import.meta.env.DEV === true, DEV logging is expected
    expect(localErrorSpy).toHaveBeenCalledWith(
      '[QuizStore] hydrateFromStart received invalid initialPayload',
      null
    );

    localErrorSpy.mockRestore();
  });

  it('hydrateStatus: finished maps to UI result', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.setState({ quizId: 'q-r' });
    useQuizStore.getState().hydrateStatus({
      status: 'finished',
      type: 'result',
      data: { title: 'Final', description: 'Desc', imageUrl: null },
    } as any);

    const s = useQuizStore.getState();
    expect(s.status).toBe('finished');
    expect(s.currentView).toBe('result');
    expect(s.viewData).toMatchObject({
      profileTitle: 'Final',
      summary: 'Desc',
      imageUrl: undefined,
      imageAlt: 'Final',
    });
    expect(s.isPolling).toBe(false);
    expect(s.retryCount).toBe(0);
  });

  it('hydrateStatus: active question increments knownQuestionsCount with Math.max rule', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.setState({ knownQuestionsCount: 2, answeredCount: 1 });

    useQuizStore.getState().hydrateStatus({
      status: 'active',
      type: 'question',
      data: { text: 'Q', options: ['a', 'b'] },
    } as any);

    const s = useQuizStore.getState();
    expect(s.currentView).toBe('question');
    expect(s.knownQuestionsCount).toBe(3); // max(2+1, 1+1)
    expect(s.retryCount).toBe(0);
  });

  it('hydrateStatus: processing only logs (no state change)', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.getState().hydrateStatus({ status: 'processing', type: 'status' } as any);
    expect(logSpy).toHaveBeenCalled();
  });

  describe('beginPolling', () => {
    it('returns early when no quizId or already polling or status not active', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      await useQuizStore.getState().beginPolling(); // no quizId
      expect(useQuizStore.getState().isPolling).toBe(false);

      useQuizStore.setState({ quizId: 'q', isPolling: true, status: 'active' });
      await useQuizStore.getState().beginPolling(); // already polling
      expect(useQuizStore.getState().isPolling).toBe(true);

      useQuizStore.setState({ isPolling: false, status: 'idle' });
      await useQuizStore.getState().beginPolling(); // not active
      expect(useQuizStore.getState().isPolling).toBe(false);
    });

    it('handles finished snapshot from poll', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      useQuizStore.setState({ quizId: 'q', status: 'active' });
      api.pollQuizStatus.mockResolvedValue({
        status: 'finished',
        type: 'result',
        data: { title: 'Done', description: 'D', imageUrl: null },
      });

      await useQuizStore.getState().beginPolling();
      const s = useQuizStore.getState();
      expect(s.status).toBe('finished');
      expect(s.isPolling).toBe(false);
      expect(s.retryCount).toBe(0);
    });

    it('handles active question snapshot from poll', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      useQuizStore.setState({ quizId: 'q', status: 'active', knownQuestionsCount: 0, answeredCount: 0 });

      api.pollQuizStatus.mockResolvedValue({
        status: 'active',
        type: 'question',
        data: { text: 'Next', options: ['x'] },
      });

      await useQuizStore.getState().beginPolling();
      const s = useQuizStore.getState();
      expect(s.currentView).toBe('question');
      expect(s.isPolling).toBe(false);
      expect(s.retryCount).toBe(0);
      expect(s.knownQuestionsCount).toBe(1);
    });

    it('processing → retry then finish on next call (timer-based backoff)', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      useQuizStore.setState({ quizId: 'q', status: 'active' });

      api.pollQuizStatus
        .mockResolvedValueOnce({ status: 'processing', type: 'status' })
        .mockResolvedValueOnce({
          status: 'finished',
          type: 'result',
          data: { title: 'OK', description: 'd', imageUrl: null },
        });

      await useQuizStore.getState().beginPolling();

      // Scheduled retry after backoff (1500ms for first retry) — advance enough time
      await vi.advanceTimersByTimeAsync(1600);

      // Allow queued microtasks to flush
      await Promise.resolve();

      const s = useQuizStore.getState();
      expect(api.pollQuizStatus).toHaveBeenCalledTimes(2); // proved a retry happened
      expect(s.status).toBe('finished');
      expect(s.isPolling).toBe(false);
      // do NOT assert retryCount here; hydrateStatus resets it to 0 on finish
    });

    it('404/403 error → fatal session expired error & clearQuizId', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      const { clearQuizId } = await import('../utils/session');

      useQuizStore.setState({ quizId: 'q', status: 'active' });
      api.pollQuizStatus.mockRejectedValue({ status: 404, message: 'not found' });

      await useQuizStore.getState().beginPolling();

      const s = useQuizStore.getState();
      expect(s.status).toBe('error');
      expect(s.currentView).toBe('error');
      expect(s.uiError).toMatch(/session has expired/i);
      expect(clearQuizId).toHaveBeenCalled();
    });

    it('non-fatal timeouts retry up to the cap, then stop; message mapped', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      useQuizStore.setState({ quizId: 'q', status: 'active' });

      // Always throw timeout (no status) — non-fatal until next attempt would exceed cap
      api.pollQuizStatus.mockRejectedValue({ code: 'poll_timeout', message: 'took too long' });

      await useQuizStore.getState().beginPolling();

      // First retry after 1500ms
      await vi.advanceTimersByTimeAsync(1600);
      await Promise.resolve();

      // Second retry after another 1500ms (per backoff formula)
      await vi.advanceTimersByTimeAsync(1600);
      await Promise.resolve();

      // At this point we've attempted: initial + 2 retries = 3 calls; cap reached, store stops
      const s = useQuizStore.getState();
      expect(api.pollQuizStatus).toHaveBeenCalledTimes(3);
      expect(s.uiError).toMatch(/Request timed out/i);
      expect(s.isPolling).toBe(false);
      // Current implementation leaves retryCount at last non-fatal value (2), not 3
      expect(s.retryCount).toBeLessThanOrEqual(2);
    });

    it('server 500+ error → fatal, no retry', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      useQuizStore.setState({ quizId: 'q', status: 'active' });

      api.pollQuizStatus.mockRejectedValue({ status: 500, message: 'boom' });

      await useQuizStore.getState().beginPolling();
      const s = useQuizStore.getState();
      expect(s.status).toBe('error');
      expect(s.currentView).toBe('error');
      expect(s.isPolling).toBe(false);
    });
  });

  it('markAnswered increments and schedules persist', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    const { saveQuizState } = await import('../utils/session');

    useQuizStore.setState({ quizId: 'q', status: 'active', lastPersistTime: 0 });

    useQuizStore.getState().markAnswered();
    expect(useQuizStore.getState().answeredCount).toBe(1);

    // Persist scheduled via setTimeout(0)
    await vi.advanceTimersByTimeAsync(0);
    expect(saveQuizState).toHaveBeenCalledWith(
      expect.objectContaining({ quizId: 'q', answeredCount: 1 })
    );
  });

  it('submitAnswerStart / submitAnswerEnd toggle isSubmittingAnswer', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.getState().submitAnswerStart();
    expect(useQuizStore.getState().isSubmittingAnswer).toBe(true);

    useQuizStore.getState().submitAnswerEnd();
    expect(useQuizStore.getState().isSubmittingAnswer).toBe(false);
  });

  it('setError (non-fatal) keeps status/view; fatal flips to error view', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({ status: 'active', currentView: 'question' });

    useQuizStore.getState().setError('oops', false);
    expect(useQuizStore.getState().uiError).toBe('oops');
    expect(useQuizStore.getState().status).toBe('active');
    expect(useQuizStore.getState().currentView).toBe('question');

    useQuizStore.getState().setError('fatal', true);
    expect(useQuizStore.getState().status).toBe('error');
    expect(useQuizStore.getState().currentView).toBe('error');
  });

  it('clearError clears uiError', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.setState({ uiError: 'bad' });
    useQuizStore.getState().clearError();
    expect(useQuizStore.getState().uiError).toBeNull();
  });

  it('recover resets only when in error', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    useQuizStore.setState({ status: 'error', currentView: 'error', uiError: 'x', retryCount: 2 });
    useQuizStore.getState().recover();

    expect(useQuizStore.getState().status).toBe('idle');
    expect(useQuizStore.getState().currentView).toBe('idle');
    expect(useQuizStore.getState().uiError).toBeNull();
    expect(useQuizStore.getState().retryCount).toBe(0);

    // Calling again when not in error leaves state intact
    useQuizStore.getState().recover();
    expect(useQuizStore.getState().status).toBe('idle');
  });

  it('reset calls clearQuizId and restores baseline', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    const { clearQuizId } = await import('../utils/session');

    useQuizStore.setState({ status: 'active', quizId: 'q', answeredCount: 5 });
    useQuizStore.getState().reset();

    const s = useQuizStore.getState();
    expect(clearQuizId).toHaveBeenCalled();
    expect(s.status).toBe('idle');
    expect(s.quizId).toBeNull();
    expect(s.answeredCount).toBe(0);
  });

  describe('persistToSession', () => {
    it('no-op when throttled, missing quizId, or wrong status', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      const { saveQuizState } = await import('../utils/session');
      (saveQuizState as any).mockClear();

      // Throttled
      useQuizStore.setState({ quizId: 'q', status: 'active', lastPersistTime: Date.now() });
      useQuizStore.getState().persistToSession();
      expect(saveQuizState).not.toHaveBeenCalled();

      // Missing quizId
      useQuizStore.setState({ quizId: null, status: 'active', lastPersistTime: 0 });
      useQuizStore.getState().persistToSession();
      expect(saveQuizState).not.toHaveBeenCalled();

      // Wrong status
      useQuizStore.setState({ quizId: 'q', status: 'idle', lastPersistTime: 0 });
      useQuizStore.getState().persistToSession();
      expect(saveQuizState).not.toHaveBeenCalled();
    });

    it('writes snapshot and updates lastPersistTime when allowed', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      const { saveQuizState } = await import('../utils/session');
      (saveQuizState as any).mockClear();

      useQuizStore.setState({
        quizId: 'q',
        status: 'active',
        answeredCount: 2,
        knownQuestionsCount: 3,
        lastPersistTime: 0,
      });

      useQuizStore.getState().persistToSession();

      expect(saveQuizState).toHaveBeenCalledWith({
        quizId: 'q',
        currentView: 'idle',
        answeredCount: 2,
        knownQuestionsCount: 3,
      });
      expect(useQuizStore.getState().lastPersistTime).toBe(Date.now());
      expect(logSpy).toHaveBeenCalledWith(
        expect.stringMatching(/\[QuizStore] Persisted to session/),
        expect.objectContaining({ quizId: 'q' })
      );
    });
  });

  describe('recoverFromSession', () => {
    it('returns false when already recovered or not idle or missing session pieces', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      // missing saved state/id
      expect(await useQuizStore.getState().recoverFromSession()).toBe(false);

      // set session pieces but store not idle
      sessionMem.id = 'id1';
      sessionMem.saved = { quizId: 'id1', currentView: 'question', answeredCount: 0, knownQuestionsCount: 0 };
      useQuizStore.setState({ status: 'active' });
      expect(await useQuizStore.getState().recoverFromSession()).toBe(false);

      // already recovered
      resetStore(useQuizStore);
      useQuizStore.setState({ sessionRecovered: true });
      expect(await useQuizStore.getState().recoverFromSession()).toBe(false);
    });

    it('processing → sets active & begins polling (without crashing)', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      sessionMem.id = 'id2';
      sessionMem.saved = { quizId: 'id2', currentView: 'synopsis', answeredCount: 1, knownQuestionsCount: 2 };

      api.getQuizStatus.mockResolvedValue({ status: 'processing', type: 'status' });

      const ok = await useQuizStore.getState().recoverFromSession();
      expect(ok).toBe(true);

      const s = useQuizStore.getState();
      expect(s.status).toBe('active');
      expect(s.quizId).toBe('id2');
      expect(s.sessionRecovered).toBe(true);

      // advance any queued polling timers harmlessly
      await vi.advanceTimersByTimeAsync(0);
    });

    it('active question → hydrates question', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      sessionMem.id = 'id3';
      sessionMem.saved = { quizId: 'id3', currentView: 'question', answeredCount: 0, knownQuestionsCount: 1 };

      api.getQuizStatus.mockResolvedValue({
        status: 'active',
        type: 'question',
        data: { text: 'Recovered Q', options: ['opt'] },
      });

      const ok = await useQuizStore.getState().recoverFromSession();
      expect(ok).toBe(true);

      const s = useQuizStore.getState();
      expect(s.currentView).toBe('question');
      expect((s.viewData as any)?.text).toBe('Recovered Q');
      expect(s.status).toBe('active');
    });

    it('finished → sets result view with normalized data', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);

      sessionMem.id = 'id4';
      sessionMem.saved = { quizId: 'id4', currentView: 'result', answeredCount: 5, knownQuestionsCount: 7 };

      api.getQuizStatus.mockResolvedValue({
        status: 'finished',
        type: 'result',
        data: { title: 'Final', description: 'D', imageUrl: null },
      });

      const ok = await useQuizStore.getState().recoverFromSession();
      expect(ok).toBe(true);

      const s = useQuizStore.getState();
      expect(s.status).toBe('finished');
      expect(s.currentView).toBe('result');
      expect((s.viewData as any)?.profileTitle).toBe('Final');
    });

    it('error during recovery logs and clears quiz id, returns false', async () => {
      const { useQuizStore } = await importStore();
      resetStore(useQuizStore);
      const { clearQuizId } = await import('../utils/session');

      sessionMem.id = 'id5';
      sessionMem.saved = { quizId: 'id5', currentView: 'question', answeredCount: 0, knownQuestionsCount: 0 };

      api.getQuizStatus.mockRejectedValue(new Error('nope'));
      const ok = await useQuizStore.getState().recoverFromSession();
      expect(ok).toBe(false);
      expect(clearQuizId).toHaveBeenCalled();
      expect(errorSpy).toHaveBeenCalled();
    });
  });
});
