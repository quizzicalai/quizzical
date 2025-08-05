import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';
import type { StateCreator } from 'zustand';
import type { Question, Synopsis } from '../types/quiz';
import type { ResultProfileData } from '../types/result';
import {
  isWrappedQuestion,
  isWrappedSynopsis,
  isRawQuestion,
  isRawSynopsis,
  InitialPayload,
} from '../utils/quizGuards';

const IS_DEV = import.meta.env.DEV === true;

type QuizStatus = 'idle' | 'loading' | 'active' | 'finished' | 'error';
type QuizView = 'idle' | 'synopsis' | 'question' | 'result' | 'error';

// The "contract" for the store's state
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
  pollStartedAt: number | null;
}

// The "contract" for the store's actions
interface QuizActions {
  startQuiz: () => void;
  hydrateFromStart: (payload: { quizId: string; initialPayload: InitialPayload }) => void;
  hydrateStatus: (dto: any) => void;
  markAnswered: () => void;
  submitAnswerStart: () => void;
  submitAnswerEnd: () => void;
  beginPolling: () => void;
  pollExceeded: () => boolean;
  setError: (message: string, isFatal?: boolean) => void;
  recover: () => void;
  reset: () => void;
}

type QuizStore = QuizState & QuizActions;

const initialState: QuizState = {
  status: 'idle',
  currentView: 'idle',
  viewData: null,
  quizId: null,
  knownQuestionsCount: 0,
  answeredCount: 0,
  totalTarget: 20,
  uiError: null,
  isSubmittingAnswer: false,
  pollStartedAt: null,
};

// Use Zustand's StateCreator for full type safety with middleware
const storeCreator: StateCreator<QuizStore> = (set, get) => ({
  ...initialState,

  startQuiz: () => set({ ...initialState, status: 'loading' }),

  hydrateFromStart: ({ quizId, initialPayload }) => {
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
      } else {
        if (import.meta.env.DEV) {
          console.warn('[hydrateFromStart] Unrecognized initial payload shape:', initialPayload);
        }
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

  hydrateStatus: (dto) => {
    if (dto?.status === 'finished') {
      set({ status: 'finished', currentView: 'result', viewData: dto.data, pollStartedAt: null });
    } else if (dto?.status === 'active' && dto?.type === 'question') {
      set((state) => ({
        status: 'active',
        currentView: 'question',
        viewData: dto.data,
        knownQuestionsCount: state.knownQuestionsCount + 1,
        pollStartedAt: null,
      }));
    }
  },

  markAnswered: () => set((state) => ({ answeredCount: state.answeredCount + 1 })),

  submitAnswerStart: () => set((state) => (state.isSubmittingAnswer ? {} : { isSubmittingAnswer: true })),

  submitAnswerEnd: () => set({ isSubmittingAnswer: false }),

  beginPolling: () => set({ pollStartedAt: Date.now() }),

  pollExceeded: () => {
    const pollStart = get().pollStartedAt;
    return pollStart ? Date.now() - pollStart > 60000 : false;
  },

  setError: (message, isFatal = false) =>
    set((state) => ({
      uiError: message,
      status: isFatal ? 'error' : state.status,
      currentView: isFatal ? 'error' : state.currentView,
      isSubmittingAnswer: false,
    })),

  recover: () =>
    set((state) => ({
      status: state.status === 'error' ? 'idle' : state.status,
      currentView: state.currentView === 'error' ? 'idle' : state.currentView,
    })),

  reset: () => set(initialState),
});

// Use the curried create<T>()(...) syntax and the built-in 'enabled' option for devtools
export const useQuizStore = create<QuizStore>()(
  devtools(storeCreator, {
    name: 'quiz-store',
    enabled: IS_DEV,
  })
);

// --- Granular Selectors for Performance ---

export const useQuizView = () => {
  return useQuizStore(
    useShallow((s) => ({
      currentView: s.currentView,
      viewData: s.viewData,
      status: s.status,
      isSubmittingAnswer: s.isSubmittingAnswer,
    }))
  );
};

export const useQuizProgress = () => {
  return useQuizStore(
    useShallow((s) => ({
      answeredCount: s.answeredCount,
      totalTarget: s.totalTarget,
    }))
  );
};