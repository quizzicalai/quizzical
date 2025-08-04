import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { shallow } from 'zustand/shallow';

const IS_DEV = import.meta.env.DEV === true;

/**
 * @typedef {'idle'|'loading'|'active'|'finished'|'error'} QuizStatus
 * @typedef {'idle'|'synopsis'|'question'|'result'|'error'} QuizView
 * @typedef {object} SynopsisData
 * @typedef {object} QuestionData
 * @typedef {object} ResultData
 *
 * @typedef {object} QuizState
 * @property {QuizStatus} status - The overall machine state of the quiz.
 * @property {QuizView} currentView - The specific UI view to be rendered.
 * @property {SynopsisData | QuestionData | ResultData | null} viewData - The data payload for the current view.
 * @property {string | null} quizId - The unique identifier for the current quiz session.
 * @property {number} knownQuestionsCount - The number of questions the client has received from the backend.
 * @property {number} answeredCount - The number of questions the user has answered.
 * @property {number} totalTarget - The target number of questions for the quiz.
 * @property {string | null} uiError - A non-fatal error message for UI display (e.g., toasts).
 * @property {boolean} isSubmittingAnswer - A flag to prevent double submissions.
 * @property {number | null} pollStartedAt - A timestamp for tracking the polling timeout window.
 */
const initial = {
  status: 'idle',
  currentView: 'idle',
  viewData: null,
  quizId: null,
  knownQuestionsCount: 0,
  answeredCount: 0,
  totalTarget: 20, // Default or from config
  uiError: null,
  isSubmittingAnswer: false,
  pollStartedAt: null,
};

const baseStore = (set, get) => ({
  ...initial,

  // --- Actions & State Transitions ---

  /** Sets the store to a loading state while the initial quiz is created. */
  startQuiz: () => set({ ...initial, status: 'loading' }),

  /** Hydrates the store from the initial payload of apiService.startQuiz. */
  hydrateFromStart: ({ quizId, initialPayload }) => set(() => {
    const type = initialPayload?.type;
    const isQuestion = type === 'question';
    const isSynopsis = type === 'synopsis';

    return {
      quizId,
      status: 'active',
      currentView: isSynopsis ? 'synopsis' : (isQuestion ? 'question' : 'idle'),
      viewData: initialPayload ?? null,
      knownQuestionsCount: isQuestion ? 1 : 0,
      answeredCount: 0,
      uiError: null,
    };
  }),

  /** Hydrates the store with a new status update from the backend. */
  hydrateStatus: (dto) => set((state) => {
    if (!dto || !dto.status) return state;

    if (dto.status === 'finished') {
      return {
        ...state,
        status: 'finished',
        currentView: 'result',
        viewData: dto.data,
        uiError: null,
        pollStartedAt: null, // Stop polling
      };
    }

    if (dto.status === 'active' && dto.type === 'question') {
      return {
        ...state,
        status: 'active',
        currentView: 'question',
        viewData: dto.data,
        knownQuestionsCount: state.knownQuestionsCount + 1,
        uiError: null,
        pollStartedAt: null, // Stop polling
      };
    }
    // For 'processing' or other states, we return the current state and let the UI continue polling.
    return state;
  }),

  /** Increments the count of answered questions. */
  markAnswered: () => set((state) => ({
    answeredCount: state.answeredCount + 1,
  })),


  // --- UI Flags and Guards ---

  /** Sets a flag to indicate an answer submission is in progress. */
  submitAnswerStart: () => set((state) =>
    state.isSubmittingAnswer ? state : { isSubmittingAnswer: true }
  ),

  /** Clears the flag indicating an answer submission is complete. */
  submitAnswerEnd: () => set({ isSubmittingAnswer: false }),

  /** Records the start time of a polling cycle. */
  beginPolling: () => set({ pollStartedAt: Date.now() }),

  /** Checks if the 60-second polling window has been exceeded. */
  pollExceeded: () => {
    const pollStart = get().pollStartedAt;
    return pollStart && (Date.now() - pollStart) > 60000;
  },


  // --- Error Handling ---

  /**
   * Sets an error state. Distinguishes between fatal errors (which change the main status)
   * and non-fatal UI errors for toasts.
   */
  setError: (message, isFatal = false) => set((state) => ({
    uiError: message ?? null,
    status: isFatal ? 'error' : state.status,
    currentView: isFatal ? 'error' : state.currentView,
    isSubmittingAnswer: false, // Always reset on error
  })),

  /** Recovers from a fatal error state, returning to 'idle'. */
  recover: () => set((state) => ({
    status: state.status === 'error' ? 'idle' : state.status,
    currentView: state.currentView === 'error' ? 'idle' : state.currentView,
  })),


  // --- Reset ---

  /** Resets the entire store to its initial state for a new quiz. */
  reset: () => set({ ...initial }),
});

// --- Store Creation with Middleware ---

export const useQuizStore = create(
  IS_DEV ? devtools(baseStore, { name: 'quiz-store' }) : baseStore
);

// --- Granular Selectors for Performance ---

/**
 * A selector that subscribes only to changes in the current view and its data.
 * Uses shallow comparison to prevent re-renders if the object structure is the same.
 */
export const useQuizView = () => useQuizStore((s) => ({
  currentView: s.currentView,
  viewData: s.viewData,
  status: s.status,
  isSubmittingAnswer: s.isSubmittingAnswer,
}), shallow);

/**
 * A selector that subscribes only to progress-related state.
 */
export const useQuizProgress = () => useQuizStore((s) => ({
  answeredCount: s.answeredCount,
  totalTarget: s.totalTarget,
}), shallow);