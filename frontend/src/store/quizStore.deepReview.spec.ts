/**
 * Deep-review 2026-07-02 — FE state-machine fixes (#3, #4, #5, #6, #7, #8, #14).
 *
 * These specs pin the store-half behaviours that the punch list flagged:
 *  - #3  recovery is no longer dead code; a saved quizId is recovered.
 *  - #14 synopsis-phase refresh restores LOCALLY with no poll.
 *  - #3/#6 recovery re-serves the CURRENT unanswered question
 *          (known_questions_count === answeredCount).
 *  - #5  a new quiz aborts the prior poll; a stale snapshot is dropped on an
 *        identity mismatch.
 *  - #6  hydrateStatus refuses a skip-ahead question.
 *  - #4  answeredCount is reconciled from the served questionNumber.
 *  - #7/#8 a successful poll clears any transient uiError; transient failures
 *        never plant a uiError below the retry cap.
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

let warnSpy: ReturnType<typeof vi.spyOn>;
let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  sessionMem.id = null;
  sessionMem.saved = null;
  api.startQuiz.mockReset();
  api.pollQuizStatus.mockReset();
  api.getQuizStatus.mockReset();
});

afterEach(() => {
  warnSpy.mockRestore();
  errorSpy.mockRestore();
  vi.resetModules();
});

const importStore = async () => await import('./quizStore');

const resetStore = (useQuizStore: any) =>
  useQuizStore.setState({
    status: 'idle',
    currentView: 'idle',
    viewData: null,
    quizId: null,
    knownQuestionsCount: 0,
    answeredCount: 0,
    totalTarget: 20,
    uiError: null,
    uiErrorCode: null,
    uiErrorTraceId: null,
    isSubmittingAnswer: false,
    isPolling: false,
    retryCount: 0,
    pollFailureStreak: 0,
    lastPersistTime: 0,
    sessionRecovered: false,
  });

describe('deep-review store fixes', () => {
  // -------------------------------------------------------------------------
  // #3 — initialState no longer prefills quizId; recovery can actually run.
  // -------------------------------------------------------------------------
  it('#3: the store boots with quizId=null even when sessionStorage has one', async () => {
    sessionMem.id = 'saved-quiz';
    const { useQuizStore } = await importStore();
    // The freshly-created store must NOT have inherited the sessionStorage id
    // (that inheritance is exactly what made recovery dead code).
    expect(useQuizStore.getState().quizId).toBeNull();
  });

  // -------------------------------------------------------------------------
  // #14 — synopsis refresh restores locally with NO poll.
  // -------------------------------------------------------------------------
  it('#14: recoverFromSession restores a persisted synopsis locally without polling', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    sessionMem.id = 'syn-quiz';
    sessionMem.saved = {
      quizId: 'syn-quiz',
      currentView: 'synopsis',
      answeredCount: 0,
      knownQuestionsCount: 0,
      synopsis: {
        title: 'The Paid Synopsis',
        summary: 'Generated once, must survive a refresh.',
        characters: [{ name: 'Ada', shortDescription: 'sd', profileText: 'pt' }],
      },
    };

    const ok = await useQuizStore.getState().recoverFromSession();
    expect(ok).toBe(true);

    const s = useQuizStore.getState();
    expect(s.currentView).toBe('synopsis');
    expect(s.status).toBe('active');
    expect((s.viewData as any)?.title).toBe('The Paid Synopsis');
    expect((s.viewData as any)?.characters?.[0]?.name).toBe('Ada');
    // The whole point: no /quiz/status call (which has no synopsis shape).
    expect(api.getQuizStatus).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // #3 + #6 — recovery re-serves the CURRENT unanswered question.
  // -------------------------------------------------------------------------
  it('#3/#6: mid-question recovery polls with known_questions_count === answeredCount', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);

    sessionMem.id = 'q-mid';
    // User answered 2, is looking at the 3rd (0-based index 2, questionNumber 3).
    sessionMem.saved = {
      quizId: 'q-mid',
      currentView: 'question',
      answeredCount: 2,
      knownQuestionsCount: 3,
    };

    api.getQuizStatus.mockResolvedValue({
      status: 'active',
      type: 'question',
      data: { text: 'Current Q', options: ['a', 'b'], questionNumber: 3 },
    });

    const ok = await useQuizStore.getState().recoverFromSession();
    expect(ok).toBe(true);

    // The server is asked to re-serve the CURRENT question, not n+1: it is
    // passed answeredCount (2), NOT the saved knownQuestionsCount (3).
    expect(api.getQuizStatus).toHaveBeenCalledWith('q-mid', {
      knownQuestionsCount: 2,
    });
    const s = useQuizStore.getState();
    expect(s.currentView).toBe('question');
    expect((s.viewData as any)?.text).toBe('Current Q');
    // answeredCount reconciled to questionNumber-1 (still 2).
    expect(s.answeredCount).toBe(2);
  });

  // -------------------------------------------------------------------------
  // #5 — a new quiz aborts the prior poll (identity check drops stale snapshot).
  // -------------------------------------------------------------------------
  it('#5: a snapshot for the OLD quizId is dropped when a new quiz has started', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({ quizId: 'quiz-A', status: 'active', currentView: 'question' });

    let release: (v: any) => void = () => {};
    const pending = new Promise((r) => { release = r; });
    api.pollQuizStatus.mockImplementation(async () => await pending);

    // Kick off a poll bound to quiz-A.
    const pollPromise = useQuizStore.getState().beginPolling();
    await Promise.resolve();

    // A NEW quiz starts underneath the in-flight poll.
    useQuizStore.getState().hydrateFromStart({
      quizId: 'quiz-B',
      initialPayload: { type: 'synopsis', data: { title: 'B', summary: 'B' } },
      charactersPayload: null,
    });

    // The old poll finally resolves with quiz-A's finished result.
    release({ status: 'finished', type: 'result', data: { title: 'A-RESULT', description: 'd', imageUrl: null } });
    await pollPromise;

    const s = useQuizStore.getState();
    // The stale A-result must NOT hijack quiz-B's synopsis.
    expect(s.quizId).toBe('quiz-B');
    expect(s.currentView).toBe('synopsis');
    expect((s.viewData as any)?.title).toBe('B');
  });

  // -------------------------------------------------------------------------
  // #6 — hydrateStatus refuses a skip-ahead question.
  // -------------------------------------------------------------------------
  it('#6: hydrateStatus refuses a question that skips ahead of answeredCount', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({
      quizId: 'q',
      status: 'active',
      currentView: 'question',
      answeredCount: 1, // user is on question index 1 (questionNumber 2)
      viewData: { id: 'cur', text: 'Current Q', answers: [], questionNumber: 2 },
    });

    // The server tries to serve question index 2 (questionNumber 3) — a skip.
    useQuizStore.getState().hydrateStatus({
      status: 'active',
      type: 'question',
      data: { text: 'Skip-ahead Q', options: ['a'], questionNumber: 3 },
    } as any);

    const s = useQuizStore.getState();
    // The current question is preserved; the skip-ahead one is refused.
    expect((s.viewData as any)?.text).toBe('Current Q');
  });

  it('#6: hydrateStatus DOES apply the next question at index === answeredCount', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({
      quizId: 'q',
      status: 'active',
      currentView: 'question',
      answeredCount: 2, // after answering, the next question is index 2 (qNum 3)
      viewData: { id: 'prev', text: 'Prev Q', answers: [], questionNumber: 2 },
    });

    useQuizStore.getState().hydrateStatus({
      status: 'active',
      type: 'question',
      data: { text: 'Next Q', options: ['a'], questionNumber: 3 },
    } as any);

    const s = useQuizStore.getState();
    expect((s.viewData as any)?.text).toBe('Next Q');
    expect(s.answeredCount).toBe(2);
  });

  // -------------------------------------------------------------------------
  // #4 — answeredCount reconciled forward from the served questionNumber.
  // -------------------------------------------------------------------------
  it('#4: hydrateStatus reconciles answeredCount forward from questionNumber', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    // The displayed question WAS answered (answeredCount === displayedNumber),
    // so this is the normal post-answer serve — not a mid-question skip. The
    // server is ahead of the FE-local count by one (a dropped duplicate submit
    // the server had already recorded), so the served questionNumber reconciles
    // answeredCount forward.
    useQuizStore.setState({
      quizId: 'q',
      status: 'active',
      currentView: 'question',
      answeredCount: 2,
      viewData: { id: 'x', text: 'x', answers: [], questionNumber: 2 },
    });

    // Server serves questionNumber 4 → 3 answered (FE-local said 2).
    useQuizStore.getState().hydrateStatus({
      status: 'active',
      type: 'question',
      data: { text: 'Q4', options: ['a'], questionNumber: 4 },
    } as any);

    const s = useQuizStore.getState();
    expect((s.viewData as any)?.text).toBe('Q4');
    expect(s.answeredCount).toBe(3);
  });

  // -------------------------------------------------------------------------
  // #7/#8 — a successful poll clears a stale transient uiError.
  // -------------------------------------------------------------------------
  it('#7/#8: hydrateStatus (question) clears a stale uiError', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({
      quizId: 'q',
      status: 'active',
      currentView: 'question',
      answeredCount: 0,
      uiError: 'a stale transient error',
      uiErrorCode: 'QF-X',
      uiErrorTraceId: 'trace-1',
    });

    useQuizStore.getState().hydrateStatus({
      status: 'active',
      type: 'question',
      data: { text: 'Fresh Q', options: ['a'], questionNumber: 1 },
    } as any);

    const s = useQuizStore.getState();
    expect(s.uiError).toBeNull();
    expect(s.uiErrorCode).toBeNull();
    expect(s.uiErrorTraceId).toBeNull();
  });

  it('#7/#8: hydrateStatus (finished) clears a stale uiError', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({ quizId: 'q', status: 'active', uiError: 'stale' });

    useQuizStore.getState().hydrateStatus({
      status: 'finished',
      type: 'result',
      data: { title: 'Done', description: 'd', imageUrl: null },
    } as any);

    expect(useQuizStore.getState().uiError).toBeNull();
  });

  it('#8: a single transient poll failure does NOT plant a uiError (stays internal)', async () => {
    const { useQuizStore } = await importStore();
    resetStore(useQuizStore);
    useQuizStore.setState({ quizId: 'q', status: 'active' });

    api.pollQuizStatus.mockRejectedValue({ status: 500, message: 'boom' });

    await useQuizStore.getState().beginPolling();

    const s = useQuizStore.getState();
    // Non-fatal transient failure: still active, streak incremented, NO uiError.
    expect(s.status).toBe('active');
    expect(s.pollFailureStreak).toBe(1);
    expect(s.uiError).toBeNull();
  });
});
