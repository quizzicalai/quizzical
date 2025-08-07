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
import { 
  getQuizId, 
  saveQuizId, 
  clearQuizId,
  saveQuizState,
  getQuizState,
  type QuizStateSnapshot 
} from '../utils/session';

const IS_DEV = import.meta.env.DEV === true;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

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
  // New fields for resilience
  retryCount: number;
  lastPersistTime: number;
  sessionRecovered: boolean;
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
  // New actions for session management
  persistToSession: () => void;
  recoverFromSession: () => Promise<boolean>;
  clearError: () => void;
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
  retryCount: 0,
  lastPersistTime: 0,
  sessionRecovered: false,
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

      const newState = {
        ...state,
        quizId,
        status: 'active' as const,
        currentView: view,
        viewData: data,
        knownQuestionsCount,
        answeredCount: 0,
        uiError: null,
        retryCount: 0,
      };

      // Persist state after hydration
      setTimeout(() => get().persistToSession(), 0);

      return newState;
    });
  },

  hydrateStatus: (dto, navigate) => {
    const { quizId } = get();
    if (dto?.status === 'finished') {
      set({ 
        status: 'finished', 
        currentView: 'result', 
        viewData: dto.data, 
        isPolling: false,
        retryCount: 0 
      });
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
        retryCount: 0, // Reset retry count on success
      }));
      
      // Persist progress
      setTimeout(() => get().persistToSession(), 0);
    }
  },

  beginPolling: async (options = {}) => {
    const { quizId, isPolling, knownQuestionsCount, retryCount } = get();
    if (!quizId || isPolling) return;

    set({ isPolling: true });
    try {
      // We pass a dummy navigate function because it's only used on finish,
      // and this is called from places that don't have access to the router.
      // The navigation is handled in the component layer.
      const nextState = await api.pollQuizStatus(quizId, { knownQuestionsCount });
      get().hydrateStatus(nextState, () => {});
    } catch (err: any) {
      if (err.status === 404 || err.status === 403) {
        get().setError('Your session has expired. Please start a new quiz.', true);
        clearQuizId();
      } else {
        const message = err.code === 'poll_timeout' ? 'Request timed out' : err.message || 'An unknown error occurred';
        const isFatal = err.status >= 500 || retryCount >= MAX_RETRIES;
        get().setError(message, isFatal);
        
        // Attempt retry if not fatal and under retry limit
        if (!isFatal && retryCount < MAX_RETRIES) {
          set({ retryCount: retryCount + 1 });
          setTimeout(() => {
            get().beginPolling({ reason: 'retry' });
          }, RETRY_DELAY_MS);
        }
      }
    } finally {
      set({ isPolling: false });
    }
  },

  markAnswered: () => {
    set((state) => ({ answeredCount: state.answeredCount + 1 }));
    // Persist progress
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
      isPolling: false,
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
    clearQuizId(); // Clear session storage on reset
    set(initialState);
  },

  // New session management methods
  persistToSession: () => {
    const state = get();
    const now = Date.now();
    
    // Throttle persistence to avoid excessive writes
    if (now - state.lastPersistTime < 1000) {
      return;
    }
    
    if (!state.quizId || state.status !== 'active') {
      return;
    }

    const snapshot: QuizStateSnapshot = {
      quizId: state.quizId,
      currentView: state.currentView as 'synopsis' | 'question' | 'result',
      answeredCount: state.answeredCount,
      knownQuestionsCount: state.knownQuestionsCount,
    };

    saveQuizState(snapshot);
    set({ lastPersistTime: now });
    
    if (IS_DEV) {
      console.log('[QuizStore] Persisted to session', snapshot);
    }
  },

  recoverFromSession: async () => {
    const state = get();
    
    // Prevent duplicate recovery
    if (state.sessionRecovered || state.status !== 'idle') {
      return false;
    }

    const savedState = getQuizState();
    const savedQuizId = getQuizId();
    
    if (!savedState || !savedQuizId) {
      return false;
    }

    if (IS_DEV) {
      console.log('[QuizStore] Attempting session recovery', savedState);
    }

    try {
      // Validate the session is still valid
      const currentStatus = await api.getQuizStatus(savedQuizId, {
        knownQuestionsCount: savedState.knownQuestionsCount,
      });

      if (currentStatus.status === 'active' || currentStatus.status === 'processing') {
        set({
          quizId: savedQuizId,
          currentView: savedState.currentView,
          answeredCount: savedState.answeredCount,
          knownQuestionsCount: savedState.knownQuestionsCount,
          status: 'active',
          sessionRecovered: true,
        });

        if (IS_DEV) {
          console.log('[QuizStore] Session recovered successfully');
        }
        
        return true;
      } else if (currentStatus.status === 'finished') {
        // Quiz finished while offline, redirect to result
        set({
          quizId: savedQuizId,
          status: 'finished',
          currentView: 'result',
          viewData: currentStatus.data,
          sessionRecovered: true,
        });
        return true;
      }
    } catch (err) {
      if (IS_DEV) {
        console.error('[QuizStore] Failed to recover session', err);
      }
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

// Attempt session recovery on initial load if we have a saved quiz ID
if (typeof window !== 'undefined') {
  const savedQuizId = getQuizId();
  if (savedQuizId) {
    // Small delay to ensure store is ready
    setTimeout(() => {
      const store = useQuizStore.getState();
      if (!store.quizId && store.status === 'idle') {
        store.recoverFromSession();
      }
    }, 100);
  }
}