// frontend/tests/fixtures/startQuiz.fixture.ts

// Valid FrontendStartQuizResponse-like payloads (camelCase, as FE expects).

export const START_QUIZ_SYNOPSIS = {
  quizId: 'abc-123',
  initialPayload: {
    type: 'synopsis' as const,
    data: {
      type: 'synopsis' as const,
      title: 'Baking Basics',
      summary: 'Let’s bake.',
    },
  },
  charactersPayload: {
    type: 'characters' as const,
    data: [
      {
        name: 'Sous Chef',
        shortDescription: 'Helper',
        profileText: 'You assist.',
        imageUrl: null,
      },
    ],
  },
} as const;

export const START_QUIZ_QUESTION = {
  quizId: 'abc-456',
  initialPayload: {
    type: 'question' as const,
    data: {
      type: 'question' as const,
      questionText: 'Favorite flour?',
      options: [{ text: 'All-purpose' }, { text: 'Bread' }],
    },
  },
  charactersPayload: null,
} as const;
