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
} from '../utils/quizGuards';
import { getQuizId, saveQuizId, clearQuizId } from '../utils/session';

const IS_DEV = import.meta.env.DEV === true;

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
}

interface QuizActions {
  startQuiz: () => void;
  hydrateFromStart: (payload: { quizId: string; initialPayload: InitialPayload }) => void;
  hydrateStatus: (dto: api.QuizStatusDTO, navigate: (path: string) => void) => void;
  beginPolling: (options?: { reason?: string }) => Promise<void>;
  markAnswered: () => void;
  submitAnswerStart: () => void;
  submitAnswerEnd: () => void;
  setError: (message: string, isFatal?: boolean) => void;
  recover: () => void;
  reset: () => void;
}

type QuizStore = QuizState & QuizActions;

const initialState: QuizState = {
  status: 'idle',
  currentView: 'idle',
  viewData: null,
  quizId: getQuizId(), // Rehydrate quizId from session storage on load
  knownQuestionsCount: 0,
  answeredCount: 0,
  totalTarget: 20,
  uiError: null,
  isSubmittingAnswer: false,
  isPolling: false,
};

const storeCreator: StateCreator<QuizStore> = (set, get) => ({
  ...initialState,

  startQuiz: () => {
    clearQuizId(); // Clear any old session ID before starting a new one
    set({ ...initialState, quizId: null, status: 'loading' });
  },

  hydrateFromStart: ({ quizId, initialPayload }) => {
    saveQuizId(quizId); // Save new quizId to session storage
    set((state) => {
      let view: QuizView = 'idle';
      let data: any = null;
      let knownQuestionsCount = 0;

      if (isWrappedQuestion(initialPayload)) {
        view = 'question';
        data = initialPayload.data;
        knownQuestionsCount = 1;
      } else if (isWrappedSynopsis(initialPayload)) {
        view = 'synopsis';
        data = initialPayload.data;
      } else if (isRawQuestion(initialPayload)) {
        view = 'question';
        data = initialPayload;
        knownQuestionsCount = 1;
      } else if (isRawSynopsis(initialPayload)) {
        view = 'synopsis';
        data = initialPayload;
      }

      return {
        ...state,
        quizId,
        status: 'active' as const,
        currentView: view,
        viewData: data,
        knownQuestionsCount,
        answeredCount: 0,
        uiError: null,
      };
    });
  },

  hydrateStatus: (dto, navigate) => {
    const { quizId } = get();
    if (dto?.status === 'finished') {
      set({ status: 'finished', currentView: 'result', viewData: dto.data, isPolling: false });
      if (quizId) {
        navigate(`/result/${quizId}`);
      }
    } else if (dto?.status === 'active' && dto?.type === 'question') {
      set((state) => ({
        status: 'active',
        currentView: 'question',
        viewData: dto.data,
        knownQuestionsCount: state.knownQuestionsCount + 1,
        isPolling: false,
      }));
    }
  },

  beginPolling: async () => {
    const { quizId, isPolling, knownQuestionsCount } = get();
    if (!quizId || isPolling) return;

    set({ isPolling: true });
    try {
      // We pass a dummy navigate function because it's only used on finish,
      // and this is called from places that don't have access to the router.
      // The navigation is handled in the component layer.
      const nextState = await api.pollQuizStatus(quizId, { knownQuestionsCount });
      get().hydrateStatus(nextState, () => {});
    } catch (err: any) {
      if (err.status === 404) {
        get().setError('Your session has expired. Please start a new quiz.', true);
        clearQuizId();
      } else {
        const message = err.code === 'poll_timeout' ? 'Request timed out' : err.message || 'An unknown error occurred';
        get().setError(message, true);
      }
    } finally {
      set({ isPolling: false });
    }
  },

  markAnswered: () => set((state) => ({ answeredCount: state.answeredCount + 1 })),

  submitAnswerStart: () => set({ isSubmittingAnswer: true }),

  submitAnswerEnd: () => set({ isSubmittingAnswer: false }),

  setError: (message, isFatal = false) =>
    set((state) => ({
      uiError: message,
      status: isFatal ? 'error' : state.status,
      currentView: isFatal ? 'error' : state.currentView,
      isSubmittingAnswer: false,
      isPolling: false,
    })),

  recover: () =>
    set((state) => ({
      status: state.status === 'error' ? 'idle' : state.status,
      currentView: state.currentView === 'error' ? 'idle' : state.currentView,
    })),

  reset: () => {
    clearQuizId(); // Clear session storage on reset
    set(initialState);
  },
});

export const useQuizStore = create<QuizStore>()(
  devtools(storeCreator, {
    name: 'quiz-store',
    enabled: IS_DEV,
  })
);

export const useQuizView = () => useQuizStore(useShallow((s) => ({
  quizId: s.quizId,
  currentView: s.currentView,
  viewData: s.viewData,
  status: s.status,
  isPolling: s.isPolling,
  isSubmittingAnswer: s.isSubmittingAnswer,
  uiError: s.uiError,
  beginPolling: s.beginPolling,
  setError: s.setError,
  reset: s.reset,
})));

export const useQuizProgress = () => useQuizStore(useShallow((s) => ({
  answeredCount: s.answeredCount,
  totalTarget: s.totalTarget,
})));