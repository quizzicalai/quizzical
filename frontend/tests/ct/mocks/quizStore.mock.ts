import * as React from 'react';

type View = 'idle' | 'synopsis' | 'question' | 'result' | 'error';

type QuizViewState = {
  quizId: string | null;
  currentView: View;
  viewData: any;
  isPolling: boolean;
  isSubmittingAnswer: boolean;
  uiError: string | null;
  // progress
  answeredCount: number;
  totalTarget: number;
};

// ---------------- Internal store ----------------
let state: QuizViewState = {
  quizId: 'ct-quiz-init',
  currentView: 'idle',
  viewData: null,
  isPolling: false,
  isSubmittingAnswer: false,
  uiError: null,
  answeredCount: 0,
  totalTarget: 3,
};

const listeners = new Set<() => void>();
const getSnapshot = () => state;
const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const emit = () => listeners.forEach((l) => l());

// Allow tests to patch state
function patch(p: Partial<QuizViewState>) {
  state = { ...state, ...p };
  emit();
}

// ---------------- CT bridges ----------------
declare global {
  interface Window {
    __ct_lastStartQuizCall?: { category: string; token: string } | null;
    __ct_resetLastStartQuizCall?: () => void;
    __ct_setNextStartQuizError?: (err: { code?: string; message?: string }) => void;

    __ct_setStartQuizPending?: () => void;
    __ct_resolveStartQuizPending?: () => void;

    __ct_quiz_set?: (p: Partial<QuizViewState>) => void;
    __ct_quiz_reset?: () => void;

    // narration test knobs (consumed by LoadingNarration, optional)
    __ct_loadingLines?: { atMs: number; text: string }[];
    __ct_loadingTickMs?: number;
  }
}

let lastCall: { category: string; token: string } | null =
  typeof window !== 'undefined' ? window.__ct_lastStartQuizCall ?? null : null;

let nextError: any | null = null;
let startPending = false;
let deferred: { resolve?: (v: any) => void } | null = null;

// ---------------- Hook mocks ----------------
export function useQuizView() {
  // live subscription so __ct_quiz_set triggers re-render
  React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const { quizId, currentView, viewData, isPolling, isSubmittingAnswer, uiError } = state;
  return { quizId, currentView, viewData, isPolling, isSubmittingAnswer, uiError };
}

export function useQuizProgress() {
  React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const { answeredCount, totalTarget } = state;
  return { answeredCount, totalTarget };
}

export function useQuizActions() {
  return {
    // phase-3 UI relies on this to auto-recover
    beginPolling: async (_opts?: any) => {
      patch({ isPolling: true });
    },
    setError: (msg: string, _bubble = true) => {
      patch({ uiError: msg });
    },
    reset: () => {
      patch({
        quizId: null,
        currentView: 'idle',
        viewData: null,
        isPolling: false,
        isSubmittingAnswer: false,
        uiError: null,
        answeredCount: 0,
        totalTarget: 3,
      });
    },
    markAnswered: () => patch({ answeredCount: state.answeredCount + 1 }),
    submitAnswerStart: () => patch({ isSubmittingAnswer: true }),
    submitAnswerEnd: () => patch({ isSubmittingAnswer: false }),
    hydrateStatus: () => {},

    // still used by LandingPage CT tests
    startQuiz: async (category: string, token: string) => {
      if (nextError) {
        const e = nextError;
        nextError = null;
        if (typeof window !== 'undefined') window.__ct_lastStartQuizCall = null;
        throw e;
      }
      lastCall = { category, token };
      if (typeof window !== 'undefined') window.__ct_lastStartQuizCall = lastCall;

      if (startPending) {
        return new Promise((resolve) => {
          deferred = { resolve: () => resolve({ quizId: 'ct-quiz-1' }) };
        });
      }
      return { quizId: 'ct-quiz-1' };
    },
  };
}

// ---------------- Node-side helpers (vitest unit tests) ----------------
export function __getLastStartQuizCall() { return lastCall; }
export function __resetLastStartQuizCall() {
  lastCall = null;
  if (typeof window !== 'undefined') window.__ct_lastStartQuizCall = null;
}
export function __setNextStartQuizError(error: any) { nextError = error; }

// ---------------- Browser bridges ----------------
if (typeof window !== 'undefined') {
  window.__ct_lastStartQuizCall = window.__ct_lastStartQuizCall ?? null;

  window.__ct_resetLastStartQuizCall = () => {
    lastCall = null;
    window.__ct_lastStartQuizCall = null;
  };

  window.__ct_setNextStartQuizError = (err) => {
    const e = Object.assign(new Error(err?.message ?? 'boom'), { code: err?.code });
    nextError = e;
  };

  window.__ct_setStartQuizPending = () => { startPending = true; };
  window.__ct_resolveStartQuizPending = () => {
    startPending = false;
    const r = deferred?.resolve; deferred = null;
    r?.({ quizId: 'ct-quiz-1' });
  };

  window.__ct_quiz_set = (p) => patch(p);
  window.__ct_quiz_reset = () => {
    state = {
      quizId: 'ct-quiz-init',
      currentView: 'idle',
      viewData: null,
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
      answeredCount: 0,
      totalTarget: 3,
    };
    emit();
  };

  // provide defaults for narration knobs if tests set them
  window.__ct_loadingLines = window.__ct_loadingLines ?? undefined;
  window.__ct_loadingTickMs = window.__ct_loadingTickMs ?? undefined;
}

export {};
