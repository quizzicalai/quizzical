// src/mocks/apiServiceMock.js

// --- Mock Data Payloads ---

const mockSynopsis = {
  type: 'synopsis',
  data: {
    title: 'The World of Ancient Rome',
    summary: 'This quiz will take you on a journey through the heart of the Roman Empire. From legendary figures like Julius Caesar to the architectural marvel of the Colosseum, your answers will reveal your place in this ancient world.',
    imageUrl: 'https://images.unsplash.com/photo-1552832230-c0197dd311b5?q=80&w=1996',
    imageAlt: 'The Roman Colosseum at sunset',
  }
};

const mockQuestion1 = {
  type: 'question',
  data: {
    id: 'q1',
    text: 'Which of these Roman architectural achievements do you find most impressive?',
    answers: [
      { id: 'a1', text: 'The massive aqueducts that supplied water to cities.', imageUrl: 'https://images.unsplash.com/photo-1589660510623-1b99674e2a22?q=80&w=2070' },
      { id: 'a2', text: 'The intricate network of roads that connected the empire.', imageUrl: 'https://images.unsplash.com/photo-1615456341773-1049c6395368?q=80&w=1974' },
      { id: 'a3', text: 'The dome of the Pantheon, a marvel of engineering.', imageUrl: 'https://images.unsplash.com/photo-1596821262397-512c01995874?q=80&w=1935' },
      { id: 'a4', text: 'The imposing and iconic Colosseum.', imageUrl: 'https://images.unsplash.com/photo-1552832230-c0197dd311b5?q=80&w=1996' },
    ]
  }
};

const mockResult = {
  type: 'result',
  data: {
    profileTitle: 'The Architect',
    summary: 'Like the master builders of Rome, you value structure, ingenuity, and legacy. You see the big picture and understand that great things are built to last. Your strategic mind and appreciation for both form and function would have made you a revered engineer in the ancient world.',
    imageUrl: 'https://images.unsplash.com/photo-1589660510623-1b99674e2a22?q=80&w=2070',
    imageAlt: 'A Roman aqueduct standing tall against a blue sky.',
    traits: [
        { id: 't1', label: 'Visionary', value: 'You plan for the long term.' },
        { id: 't2', label: 'Pragmatic', value: 'You appreciate practical solutions.' },
        { id: 't3', label: 'Enduring', value: 'You build things that last.' },
    ],
    shareUrl: 'https://example.com/result/mock123'
  }
};


// --- Mock API Functions ---

let pollCount = 0;

const delay = (ms) => new Promise(res => setTimeout(res, ms));

export async function startQuiz(category, options) {
  await delay(800);
  console.log('[Mock API] startQuiz called with:', category);
  return {
    quizId: 'mock-quiz-123',
    initialPayload: mockSynopsis,
  };
}

export async function getQuizStatus(quizId, { knownQuestionsCount }) {
    await delay(500);
    console.log(`[Mock API] getQuizStatus called. knownQuestionsCount: ${knownQuestionsCount}`);
    
    // Simulate polling: first few calls are "processing"
    if (pollCount < 2) {
        pollCount++;
        return { status: 'processing', type: 'wait' };
    }
    
    // After processing, return the first question
    pollCount = 0; // reset for next poll
    return mockQuestion1;
}


export async function pollQuizStatus(quizId, { knownQuestionsCount }) {
    await delay(1500); // Simulate the total polling time
    console.log(`[Mock API] pollQuizStatus called. knownQuestionsCount: ${knownQuestionsCount}`);
    
    if (knownQuestionsCount < 5) {
        return { ...mockQuestion1, id: `q${knownQuestionsCount + 1}` }; // Return a "new" question
    }
    
    // After enough questions, return the final result
    return mockResult;
}


export async function submitAnswer(quizId, answerId, options) {
  await delay(300);
  console.log('[Mock API] submitAnswer called:', { quizId, answerId });
  return { status: 'ok', message: 'Answer received' };
}

export async function getResult(resultId, options) {
  await delay(700);
  console.log('[Mock API] getResult called for:', resultId);
  return mockResult.data;
}

export async function submitFeedback(quizId, { rating, comment }, options) {
  await delay(400);
  console.log('[Mock API] submitFeedback called:', { quizId, rating, comment });
  return { status: 'ok', message: 'Feedback received' };
}