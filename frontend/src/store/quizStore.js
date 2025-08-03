import { create } from 'zustand';

/**
 * Zustand store for managing the state of an active quiz session.
 * This version uses immutable updates and clearer action definitions.
 */
export const useQuizStore = create((set) => ({
  // STATE
  quizId: null,
  status: 'idle', // 'idle', 'loading', 'active', 'finished', 'error'
  currentQuestion: null,
  error: null,

  // ACTIONS
  
  /**
   * Begins the quiz flow by setting the ID and putting the store in a loading state.
   */
  startLoadingQuiz: ({ quizId }) => set((state) => ({
    ...state,
    quizId,
    status: 'loading',
    currentQuestion: null,
    error: null,
  })),

  /**
   * Hydrates the store with the full state received from the backend.
   * This is the primary way the UI is updated with new questions or results.
   */
  hydrateState: ({ quizData }) => set((state) => {
    if (quizData.status === 'question_active' || quizData.status === 'synopsis_review') {
      return { ...state, status: 'active', currentQuestion: quizData.data, error: null };
    }
    if (quizData.status === 'finished') {
      return { ...state, status: 'finished', currentQuestion: null, error: null };
    }
    // Return current state if status is unrecognized
    return state;
  }),

  /**
   * Sets the store to an error state with a specific message.
   */
  setError: ({ message }) => set((state) => ({
    ...state,
    status: 'error',
    error: message,
  })),

  /**
   * Resets the store to its initial, default state for a new session.
   */
  reset: () => set({
    quizId: null,
    status: 'idle',
    currentQuestion: null,
    error: null,
  }),
}));
