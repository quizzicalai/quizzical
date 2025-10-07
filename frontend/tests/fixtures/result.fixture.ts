// frontend/tests/fixtures/result.fixture.ts

// ShareableResultSchema-like payload (DB result)
export const DB_RESULT = {
  title: 'You are The Chef',
  description: 'Skilled and savory.',
  imageUrl: null,
  traits: [{ label: 'Savory' }],
  shareUrl: 'https://share/me',
  category: 'Cooking',
  createdAt: '2024-01-01T00:00:00.000Z',
} as const;
