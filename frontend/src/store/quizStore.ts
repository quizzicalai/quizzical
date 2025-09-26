// src/store/quizStore.ts
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';
import type { StateCreator } from 'zustand';
import type { Question, Synopsis } from '../types/quiz';
import type { ResultProfileData } from '../types/result';
import * as api from '../services/apiService';
import {
  isWrappedQuestion,
  isWrappedSynopsis,
  isRawQuestion,
  isRawSynopsis,
  InitialPayload,
  toUiQuestionFromApi,
  toUiCharacters,
  toUiResult,
} from '../utils/quizGuards';
import {
  getQuizId,
  saveQuizId,
  clearQuizId,
  saveQuizState,
  getQuizState,
  type QuizStateSnapshot,
} from '../utils/session';
import { ApiError } from '../types/api';

const IS_DEV = import.meta.env.DEV === true;

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1500;
const MIN_SESSION_PERSIST_INTERVAL_MS = 1000;

type QuizStatus = 'idle' | 'loading' | 'active' | 'finished' | 'error';
type QuizView = 'idle' | 'synopsis' | 'question' | 'result' | 'error';

interface QuizState {
  status: QuizStatus;
  currentView: QuizView;
  viewData: Synopsis | Question | ResultProfileData | null;

  quizId: string | null;

  knownQuestionsCount: number;
  answeredCount: number;

  totalTarget: number;

  uiError: string | null;
  isSubmittingAnswer: boolean;

  isPolling: boolean;
  retryCount: number;
  lastPersistTime: number;
  sessionRecovered: boolean;
}

interface QuizActions {
  startQuiz: (category: string, turnstileToken: string) => Promise<void>;
  hydrateFromStart: (payload: {
    quizId: string;
    initialPayload: InitialPayload;
    charactersPayload?: { type: 'characters'; data: any[] } | null;
  }) => void;
  hydrateStatus: (dto: api.QuizStatusDTO) => void;
  beginPolling: (options?: { reason?: string }) => Promise<void>;
  markAnswered: () => void;
  submitAnswerStart: () => void;
  submitAnswerEnd: () => void;
  setError: (message: string, isFatal?: boolean) => void;
  clearError: () => void;
  recover: () => void;
  reset: () => void;
  persistToSession: () => void;
  recoverFromSession: () => Promise<boolean>;
}

type QuizStore = QuizState & QuizActions;

const initialState: QuizState = {
  status: 'idle',
  currentView: 'idle',
  viewData: null,
  quizId: getQuizId(),
  knownQuestionsCount: 0,
  answeredCount: 0,
  totalTarget: 20,
  uiError: null,
  isSubmittingAnswer: false,
  isPolling: false,
  retryCount: 0,
  lastPersistTime: 0,
  sessionRecovered: false,
};

/* -----------------------------------------------------------------------------
 * Normalizers
 * ---------------------------------------------------------------------------*/

function normalizeResultLike(raw: any): ResultProfileData {
  const d = raw?.data ?? raw ?? {};
  return toUiResult(d);
}

function normalizeQuestionLike(raw: any): Question {
  // ✅ Key fix: if it's already in UI shape (has `answers`), return as-is.
  if (raw && Array.isArray(raw.answers)) {
    return raw as Question;
  }
  // Otherwise, convert API/legacy shapes with `options` → `answers`.
  return toUiQuestionFromApi(raw);
}

function normalizeSynopsisLike(raw: any): Synopsis {
  const s = raw?.data ?? raw ?? {};
  return {
    title: String(s?.title ?? ''),
    summary: String(s?.summary ?? ''),
  };
}

/* -----------------------------------------------------------------------------
 * Store
 * ---------------------------------------------------------------------------*/

const storeCreator: StateCreator<QuizStore> = (set, get) => ({
  ...initialState,

  startQuiz: async (category: string, turnstileToken: string) => {
    clearQuizId();
    set({ ...initialState, quizId: null, status: 'loading' });
    try {
      const { quizId, initialPayload, charactersPayload } = await api.startQuiz(
        category,
        turnstileToken
      );
      get().hydrateFromStart({ quizId, initialPayload, charactersPayload });
    } catch (err) {
      if (IS_DEV) console.error('[QuizStore] startQuiz failed', err);
      const apiError = err as ApiError;
      const message =
        apiError?.message || 'Could not create a quiz. Please try again.';
      set({ status: 'error', uiError: message, currentView: 'error' });
      throw err;
    }
  },

  hydrateFromStart: ({ quizId, initialPayload, charactersPayload }) => {
    saveQuizId(quizId);

    set((state) => {
      let view: QuizView = 'idle';
      let data: any = null;
      let knownQuestionsCount = 0;

      if (isWrappedQuestion(initialPayload)) {
        view = 'question';
        data = normalizeQuestionLike(initialPayload.data);
        knownQuestionsCount = 1;
      } else if (isWrappedSynopsis(initialPayload)) {
        view = 'synopsis';
        data = normalizeSynopsisLike(initialPayload.data);
      } else if (isRawQuestion(initialPayload)) {
        view = 'question';
        data = normalizeQuestionLike(initialPayload);
        knownQuestionsCount = 1;
      } else if (isRawSynopsis(initialPayload)) {
        view = 'synopsis';
        data = normalizeSynopsisLike(initialPayload);
      } else if (IS_DEV) {
        console.error(
          '[QuizStore] hydrateFromStart received invalid initialPayload',
          initialPayload
        );
      }

      if (view === 'synopsis' && data) {
        const validCharacters =
          !!charactersPayload &&
          charactersPayload.type === 'characters' &&
          Array.isArray(charactersPayload.data);
        if (validCharacters) {
          data = { ...data, characters: toUiCharacters(charactersPayload.data) };
        } else if (charactersPayload && IS_DEV) {
          console.warn(
            '[QuizStore] charactersPayload present but invalid shape',
            charactersPayload
          );
        }
      }

      const newState: QuizState = {
        ...state,
        quizId,
        status: 'active',
        currentView: view,
        viewData: data,
        knownQuestionsCount,
        answeredCount: 0,
        uiError: null,
        retryCount: 0,
      };

      setTimeout(() => get().persistToSession(), 0);
      return newState;
    });
  },

  hydrateStatus: (dto) => {
    if (!dto) return;

    if (dto.status === 'finished' && dto.type === 'result') {
      set({
        status: 'finished',
        currentView: 'result',
        viewData: normalizeResultLike(dto.data),
        isPolling: false,
        retryCount: 0,
      });
      setTimeout(() => get().persistToSession(), 0);
      return;
    }

    if (dto.status === 'active' && dto.type === 'question') {
      set((state) => {
        const nextKnown = Math.max(state.knownQuestionsCount + 1, state.answeredCount + 1);
        return {
          ...state,
          status: 'active',
          currentView: 'question',
          viewData: normalizeQuestionLike(dto.data),
          knownQuestionsCount: nextKnown,
          isPolling: false,
          retryCount: 0,
        };
      });
      setTimeout(() => get().persistToSession(), 0);
      return;
    }

    if (IS_DEV) {
      console.log('[QuizStore] hydrateStatus no-op (processing/unknown)', dto);
    }
  },

  beginPolling: async (_options = {}) => {
    const state = get();
    if (!state.quizId) return;
    if (state.isPolling) return;

    set({ isPolling: true });

    const doPoll = async (): Promise<void> => {
      const { quizId, knownQuestionsCount, retryCount, status } = get();
      if (!quizId || status !== 'active') {
        set({ isPolling: false });
        return;
      }

      try {
        const snapshot = await api.pollQuizStatus(quizId, { knownQuestionsCount });

        if (snapshot.status === 'finished') {
          get().hydrateStatus(snapshot);
          set({ isPolling: false, retryCount: 0 });
          return;
        }

        if (snapshot.status === 'active' && snapshot.type === 'question') {
          get().hydrateStatus(snapshot);
          set({ isPolling: false, retryCount: 0 });
          return;
        }

        const nextRetry = Math.min(retryCount + 1, MAX_RETRIES);
        set({ retryCount: nextRetry });

        const delay = RETRY_DELAY_MS * (1 + Math.floor((nextRetry - 1) / 2));
        setTimeout(() => {
          set({ isPolling: false });
          get().beginPolling({ reason: 'continue' });
        }, delay);
      } catch (err: any) {
        if (IS_DEV) console.error('[QuizStore] beginPolling error', err);
        const statusCode = err?.status as number | undefined;

        if (statusCode === 404 || statusCode === 403) {
          get().setError('Your session has expired. Please start a new quiz.', true);
          clearQuizId();
          set({ isPolling: false });
          return;
        }

        const nextRetry = Math.min(get().retryCount + 1, MAX_RETRIES);
        const isFatal = statusCode ? statusCode >= 500 : nextRetry >= MAX_RETRIES;
        const message =
          err?.code === 'poll_timeout'
            ? 'Request timed out'
            : err?.message || 'An unknown error occurred';

        get().setError(message, isFatal);

        if (!isFatal && nextRetry <= MAX_RETRIES) {
          set({ retryCount: nextRetry });
          const delay = RETRY_DELAY_MS * (1 + Math.floor((nextRetry - 1) / 2));
          setTimeout(() => {
            set({ isPolling: false });
            get().beginPolling({ reason: 'retry' });
          }, delay);
        } else {
          set({ isPolling: false });
        }
      }
    };

    await doPoll();
  },

  markAnswered: () => {
    set((state) => ({ answeredCount: state.answeredCount + 1 }));
    setTimeout(() => get().persistToSession(), 0);
  },

  submitAnswerStart: () => set({ isSubmittingAnswer: true }),
  submitAnswerEnd: () => set({ isSubmittingAnswer: false }),

  setError: (message, isFatal = false) =>
    set((state) => ({
      uiError: message,
      status: isFatal ? 'error' : state.status,
      currentView: isFatal ? 'error' : state.currentView,
      isSubmittingAnswer: false,
    })),

  clearError: () => set({ uiError: null }),

  recover: () =>
    set((state) => ({
      status: state.status === 'error' ? 'idle' : state.status,
      currentView: state.currentView === 'error' ? 'idle' : state.currentView,
      uiError: null,
      retryCount: 0,
    })),

  reset: () => {
    clearQuizId();
    set(initialState);
  },

  persistToSession: () => {
    const state = get();
    const now = Date.now();

    if (now - state.lastPersistTime < MIN_SESSION_PERSIST_INTERVAL_MS) return;
    if (!state.quizId || (state.status !== 'active' && state.status !== 'finished')) return;

    const snapshot: QuizStateSnapshot = {
      quizId: state.quizId,
      currentView: state.currentView as 'synopsis' | 'question' | 'result',
      answeredCount: state.answeredCount,
      knownQuestionsCount: state.knownQuestionsCount,
    };

    saveQuizState(snapshot);
    set({ lastPersistTime: now });

    if (IS_DEV) console.log('[QuizStore] Persisted to session', snapshot);
  },

  recoverFromSession: async () => {
    const state = get();
    if (state.sessionRecovered || state.status !== 'idle') return false;

    const savedState = getQuizState();
    const savedQuizId = getQuizId();
    if (!savedState || !savedQuizId) return false;

    if (IS_DEV) console.log('[QuizStore] Attempting session recovery', savedState);

    try {
      const currentStatus = await api.getQuizStatus(savedQuizId, {
        knownQuestionsCount: savedState.knownQuestionsCount,
      });

      if (currentStatus.status === 'processing' || currentStatus.status === 'active') {
        set({
          quizId: savedQuizId,
          currentView: savedState.currentView,
          answeredCount: savedState.answeredCount,
          knownQuestionsCount: savedState.knownQuestionsCount,
          status: 'active',
          sessionRecovered: true,
          uiError: null,
        });

        if (currentStatus.status === 'active' && currentStatus.type === 'question') {
          get().hydrateStatus(currentStatus);
        } else {
          get().beginPolling({ reason: 'recover' });
        }
        if (IS_DEV) console.log('[QuizStore] Session recovered (active/processing)');
        return true;
      }

      if (currentStatus.status === 'finished') {
        set({
          quizId: savedQuizId,
          status: 'finished',
          currentView: 'result',
          viewData: normalizeResultLike(currentStatus.data),
          sessionRecovered: true,
          uiError: null,
        });
        if (IS_DEV) console.log('[QuizStore] Session recovered (finished)');
        return true;
      }
    } catch (err) {
      if (IS_DEV) console.error('[QuizStore] Failed to recover session', err);
      clearQuizId();
    }
    return false;
  },
});

export const useQuizStore = create<QuizStore>()(
  devtools(storeCreator, {
    name: 'quiz-store',
    enabled: IS_DEV,
  })
);

export const useQuizView = () =>
  useQuizStore(
    useShallow((s) => ({
      quizId: s.quizId,
      currentView: s.currentView,
      viewData: s.viewData,
      status: s.status,
      isPolling: s.isPolling,
      isSubmittingAnswer: s.isSubmittingAnswer,
      uiError: s.uiError,
    }))
  );

export const useQuizProgress = () =>
  useQuizStore(
    useShallow((s) => ({
      answeredCount: s.answeredCount,
      totalTarget: s.totalTarget,
    }))
  );

export const useQuizActions = () =>
  useQuizStore(
    useShallow((s) => ({
      startQuiz: s.startQuiz,
      hydrateFromStart: s.hydrateFromStart,
      hydrateStatus: s.hydrateStatus,
      beginPolling: s.beginPolling,
      markAnswered: s.markAnswered,
      submitAnswerStart: s.submitAnswerStart,
      submitAnswerEnd: s.submitAnswerEnd,
      setError: s.setError,
      clearError: s.clearError,
      recover: s.recover,
      reset: s.reset,
      persistToSession: s.persistToSession,
      recoverFromSession: s.recoverFromSession,
    }))
  );

if (typeof window !== 'undefined') {
  const savedQuizId = getQuizId();
  if (savedQuizId) {
    setTimeout(() => {
      const store = useQuizStore.getState();
      if (!store.quizId && store.status === 'idle') {
        store.recoverFromSession();
      }
    }, 100);
  }
}
