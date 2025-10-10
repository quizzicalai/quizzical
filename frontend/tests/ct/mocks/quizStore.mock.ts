// frontend/tests/ct/mocks/quizStore.mock.ts
let lastCall: { category: string; token: string } | null = null;
let nextError: any | null = null;

export function useQuizActions() {
  return {
    startQuiz: async (category: string, token: string) => {
      if (nextError) {
        const e = nextError;
        nextError = null; // consume once
        throw e;
      }
      lastCall = { category, token };
      return { quizId: 'ct-quiz-1' };
    },
  };
}

export function __getLastStartQuizCall() { return lastCall; }
export function __resetLastStartQuizCall() { lastCall = null; }
export function __setNextStartQuizError(error: any) { nextError = error; }
