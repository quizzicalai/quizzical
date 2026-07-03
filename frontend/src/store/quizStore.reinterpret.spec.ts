// frontend/src/store/quizStore.reinterpret.spec.ts
// "Try a different interpretation" (owner blackbox, 2026-07-02) — store-side
// behaviour of the reinterpret reload:
//   - starts a NEW quiz for the SAME typed topic with the current reading
//     rejected (passed to api.startQuiz as rejectedInterpretations);
//   - the rejected list ACCUMULATES across cycles;
//   - the normal loading state shows and the new synopsis replaces the old;
//   - errors restore the previous synopsis with an inline message and do NOT
//     grow the rejected list;
//   - a fresh startQuiz resets the chain.
import { beforeAll, describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

beforeAll(() => {
  (import.meta as any).env = { ...(import.meta as any).env, DEV: true };
});

// --- In-memory session "storage" for mocks
const sessionMem = {
  id: null as string | null,
  saved: null as any,
};

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

const FIRST_SYNOPSIS = {
  type: 'synopsis',
  data: { title: 'Quiz: Trolls', summary: 'Grumpy bridge-dwellers of folklore.' },
};
const SECOND_SYNOPSIS = {
  type: 'synopsis',
  data: { title: 'Quiz: Trolls (2016 film)', summary: 'Poppy, Branch and friends.' },
};
const THIRD_SYNOPSIS = {
  type: 'synopsis',
  data: { title: 'Quiz: Internet Trolls', summary: 'Keyboard warriors of the web.' },
};

/** Drive the store into a live synopsis state via the real startQuiz action. */
async function startOnSynopsis(useQuizStore: any) {
  api.startQuiz.mockResolvedValueOnce({
    quizId: 'q-1',
    initialPayload: FIRST_SYNOPSIS,
    charactersPayload: null,
  });
  await useQuizStore.getState().startQuiz('Trolls', 'tok-0');
  const s = useQuizStore.getState();
  expect(s.currentView).toBe('synopsis');
  expect(s.category).toBe('Trolls');
  expect(s.rejectedInterpretations).toEqual([]);
}

describe('quizStore reinterpret', () => {
  it('startQuiz records the typed topic and resets the rejection chain', async () => {
    const { useQuizStore } = await importStore();
    useQuizStore.setState({ rejectedInterpretations: ['stale entry'] } as any);
    await startOnSynopsis(useQuizStore);
  });

  it('reinterpret starts a NEW quiz for the same topic with the current reading rejected', async () => {
    const { useQuizStore } = await importStore();
    await startOnSynopsis(useQuizStore);

    api.startQuiz.mockResolvedValueOnce({
      quizId: 'q-2',
      initialPayload: SECOND_SYNOPSIS,
      charactersPayload: null,
    });

    await useQuizStore.getState().reinterpret('tok-1');

    // Same typed topic + fresh token + the displayed reading rejected.
    expect(api.startQuiz).toHaveBeenLastCalledWith('Trolls', 'tok-1', {
      rejectedInterpretations: [
        'Quiz: Trolls — Grumpy bridge-dwellers of folklore.',
      ],
    });

    const s = useQuizStore.getState();
    // New synopsis replaced the old, under the NEW quiz id.
    expect(s.quizId).toBe('q-2');
    expect(s.currentView).toBe('synopsis');
    expect((s.viewData as any).title).toBe('Quiz: Trolls (2016 film)');
    expect(s.rejectedInterpretations).toEqual([
      'Quiz: Trolls — Grumpy bridge-dwellers of folklore.',
    ]);
    expect(s.isReinterpreting).toBe(false);
  });

  it('shows the normal loading state while the reinterpret is in flight', async () => {
    const { useQuizStore } = await importStore();
    await startOnSynopsis(useQuizStore);

    let release!: (v: any) => void;
    api.startQuiz.mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve; })
    );

    const pending = useQuizStore.getState().reinterpret('tok-1');

    const mid = useQuizStore.getState();
    expect(mid.status).toBe('loading');
    expect(mid.currentView).toBe('idle'); // QuizFlowPage renders the LoadingCard
    expect(mid.isReinterpreting).toBe(true);
    // The outgoing quizId stays installed so the page's "missing quizId ->
    // navigate home" guard cannot fire mid-reload.
    expect(mid.quizId).toBe('q-1');

    release({ quizId: 'q-2', initialPayload: SECOND_SYNOPSIS, charactersPayload: null });
    await pending;
    expect(useQuizStore.getState().currentView).toBe('synopsis');
  });

  it('the rejected list accumulates across reload cycles', async () => {
    const { useQuizStore } = await importStore();
    await startOnSynopsis(useQuizStore);

    api.startQuiz.mockResolvedValueOnce({
      quizId: 'q-2',
      initialPayload: SECOND_SYNOPSIS,
      charactersPayload: null,
    });
    await useQuizStore.getState().reinterpret('tok-1');

    api.startQuiz.mockResolvedValueOnce({
      quizId: 'q-3',
      initialPayload: THIRD_SYNOPSIS,
      charactersPayload: null,
    });
    await useQuizStore.getState().reinterpret('tok-2');

    expect(api.startQuiz).toHaveBeenLastCalledWith('Trolls', 'tok-2', {
      rejectedInterpretations: [
        'Quiz: Trolls — Grumpy bridge-dwellers of folklore.',
        'Quiz: Trolls (2016 film) — Poppy, Branch and friends.',
      ],
    });
    expect(useQuizStore.getState().rejectedInterpretations).toHaveLength(2);
    expect((useQuizStore.getState().viewData as any).title).toBe('Quiz: Internet Trolls');
  });

  it('failure restores the previous synopsis with an inline error and does NOT grow the chain', async () => {
    const { useQuizStore } = await importStore();
    await startOnSynopsis(useQuizStore);

    api.startQuiz.mockRejectedValueOnce({
      status: 429,
      qfCode: 'QF-REINTERPRET-CAP',
      whimsical: "We've spun the interpretation wheel as far as it goes for this topic.",
    });

    await useQuizStore.getState().reinterpret('tok-1');

    const s = useQuizStore.getState();
    // Never a dead end: back on the SAME synopsis, non-fatal inline error.
    expect(s.status).toBe('active');
    expect(s.currentView).toBe('synopsis');
    expect((s.viewData as any).title).toBe('Quiz: Trolls');
    expect(s.uiError).toMatch(/interpretation wheel/i);
    expect(s.uiErrorCode).toBe('QF-REINTERPRET-CAP');
    // The failed cycle must not double-count this reading on the next attempt.
    expect(s.rejectedInterpretations).toEqual([]);
    expect(s.isReinterpreting).toBe(false);
  });

  it('no-ops when there is no recorded topic or the view is not the synopsis', async () => {
    const { useQuizStore } = await importStore();

    // No topic (e.g. session recovered without one).
    useQuizStore.setState({
      currentView: 'synopsis',
      status: 'active',
      category: null,
    } as any);
    await useQuizStore.getState().reinterpret('tok');
    expect(api.startQuiz).not.toHaveBeenCalled();

    // Not on the synopsis view.
    useQuizStore.setState({ currentView: 'question', category: 'Trolls' } as any);
    await useQuizStore.getState().reinterpret('tok');
    expect(api.startQuiz).not.toHaveBeenCalled();
  });

  it('re-entrant clicks are ignored while a reinterpret is in flight', async () => {
    const { useQuizStore } = await importStore();
    await startOnSynopsis(useQuizStore);

    let release!: (v: any) => void;
    api.startQuiz.mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve; })
    );

    const first = useQuizStore.getState().reinterpret('tok-1');
    await useQuizStore.getState().reinterpret('tok-2'); // ignored (in flight)

    expect(api.startQuiz).toHaveBeenCalledTimes(2); // 1 start + 1 reinterpret

    release({ quizId: 'q-2', initialPayload: SECOND_SYNOPSIS, charactersPayload: null });
    await first;
  });
});
