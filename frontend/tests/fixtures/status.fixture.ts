// frontend/tests/fixtures/status.fixture.ts

// Processing (camel and snake id variants)
export const STATUS_PROCESSING_CAML = {
  status: 'processing' as const,
  quizId: 'abc-123',
};

export const STATUS_PROCESSING_SNAKE = {
  status: 'processing' as const,
  quiz_id: 'abc-123',
};

// Active question (strict shape)
export const STATUS_QUESTION = {
  status: 'active' as const,
  type: 'question' as const,
  data: {
    text: 'Pick one',
    imageUrl: null,
    options: [{ text: 'A' }, { text: 'B' }],
  },
};

// Active question (looser data object but still valid)
export const STATUS_QUESTION_LOOSE = {
  status: 'active' as const,
  type: 'question' as const,
  data: {
    text: 'Loose question',
    options: [{ text: 'X' }],
  },
};

// Finished result
export const STATUS_RESULT = {
  status: 'finished' as const,
  type: 'result' as const,
  data: {
    title: 'You are The Baker',
    description: 'Warm and crusty!',
    imageUrl: null,
    traits: [{ label: 'Crispy' }],
    shareUrl: 'https://share/url',
  },
};
