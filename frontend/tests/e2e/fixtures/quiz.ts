// tests/e2e/fixtures/quiz.ts
import type { Page, Route, Request } from '@playwright/test';

type QuizServerState = {
  quizId: string;
  started: boolean;
  proceeded: boolean;
  answeredCount: number;
  firstQuestionIssued: boolean;
};

const QUIZ_ID = 'e2e-1';

/* -----------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------------*/
function json(route: Route, status: number, body: unknown, headers?: Record<string, string>) {
  return route.fulfill({
    status,
    headers: { 'content-type': 'application/json', ...(headers || {}) },
    body: JSON.stringify(body),
  });
}

async function readJson<T = any>(request: Request): Promise<T | undefined> {
  try {
    return (await request.postDataJSON()) as T;
  } catch {
    return undefined;
  }
}

function getKnownQuestionsCount(url: string): number {
  const u = new URL(url);
  const v = u.searchParams.get('known_questions_count');
  return v ? Number(v) : 0;
}

/* -----------------------------------------------------------------------------
 * Public: install all quiz-related mocks
 *  - Shapes match FE schemas & normalizers
 *  - Status polling advances based on `known_questions_count`
 * ---------------------------------------------------------------------------*/
export async function installQuizMocks(page: Page) {
  const state: QuizServerState = {
    quizId: QUIZ_ID,
    started: false,
    proceeded: false,
    answeredCount: 0,
    firstQuestionIssued: false,
  };

  // POST /quiz/start → FrontendStartQuizResponse (camelCase). Wrapped payload.
  await page.route('**/api/v1/quiz/start', async (route) => {
    state.started = true;
    state.proceeded = false;
    state.answeredCount = 0;
    state.firstQuestionIssued = false;

    const payload = {
      quizId: state.quizId,
      initialPayload: {
        // Wrapper { type, data } where data is discriminated too
        type: 'synopsis',
        data: {
          type: 'synopsis',
          title: 'The World of Ancient Rome',
          summary: 'A short synopsis.',
        },
      },
      // charactersPayload intentionally omitted
    };

    return json(route, 200, payload);
  });

  // POST /quiz/proceed → { status: 'processing', quizId }
  await page.route('**/api/v1/quiz/proceed', async (route, request) => {
    state.proceeded = true;

    const body = await readJson<{ quizId?: string; quiz_id?: string }>(request);
    const sentId = body?.quizId ?? body?.quiz_id;
    if (sentId && sentId !== state.quizId) {
      return json(route, 400, { code: 'bad_quiz', message: 'Unknown quiz id' });
    }

    return json(route, 200, { status: 'processing', quizId: state.quizId });
  });

  // GET /quiz/status/:id → first "active question" (when known=0), then "finished result"
  // IMPORTANT: match query params too (…/status/e2e-1?known_questions_count=0)
  await page.route('**/api/v1/quiz/status/**', async (route, request) => {
    const url = request.url();

    // Only handle our quiz; let other IDs fall through.
    if (!url.endsWith('/' + state.quizId) && !url.includes('/' + state.quizId + '?')) {
      return route.fallback();
    }

    // Before proceed, behave as "processing"
    if (!state.proceeded) {
      return json(route, 200, { status: 'processing', quiz_id: state.quizId });
    }

    const known = getKnownQuestionsCount(url);

    if (known === 0) {
      state.firstQuestionIssued = true;

      // API-shaped question; FE normalizes via toUiQuestionFromApi
      const question = {
        status: 'active' as const,
        type: 'question' as const,
        data: {
          type: 'question',
          questionText: 'Which achievement is most impressive?',
          options: [
            { text: 'Aqueducts' },
            { text: 'Roads' },
            { text: 'Concrete' },
            { text: 'The Republic' },
          ],
          imageUrl: null,
        },
      };

      return json(route, 200, question);
    }

    // After client knows at least one question → finished result
    const result = {
      status: 'finished' as const,
      type: 'result' as const,
      data: {
        title: 'The Architect',
        description: 'You value engineering excellence and civic design.',
        imageUrl: null,
        traits: [
          { id: 'craft', label: 'Craft', value: 'High' },
          { id: 'civic', label: 'Civic Minded', value: 'High' },
        ],
        shareUrl: 'https://example.com/share/e2e-1',
      },
    };

    return json(route, 200, result);
  });

  // POST /quiz/next → acknowledge answer submission
  await page.route('**/api/v1/quiz/next', async (route, request) => {
    const body = await readJson<{
      quizId?: string;
      quiz_id?: string;
      questionIndex?: number;
      optionIndex?: number;
      answer?: string;
    }>(request);

    if (
      (body?.quizId && body.quizId !== state.quizId) ||
      (body?.quiz_id && body.quiz_id !== state.quizId)
    ) {
      return json(route, 400, { code: 'bad_quiz', message: 'Unknown quiz id' });
    }

    state.answeredCount += 1;
    return json(route, 200, { status: 'ok' });
  });

  // Optional: GET /result/:id — only used when VITE_USE_DB_RESULTS=true
  await page.route('**/api/v1/result/**', async (route, request) => {
    const url = request.url();
    if (!url.endsWith('/' + state.quizId)) return route.fallback();

    return json(route, 200, {
      title: 'The Architect',
      description: 'You value engineering excellence and civic design.',
      imageUrl: null,
      category: 'Ancient Rome',
      createdAt: new Date().toISOString(),
      traits: [
        { id: 'craft', label: 'Craft', value: 'High' },
        { id: 'civic', label: 'Civic Minded', value: 'High' },
      ],
      shareUrl: 'https://example.com/share/e2e-1',
    });
  });

  // Optional: POST /feedback — no-op
  await page.route('**/api/v1/feedback', async (route) => {
    return json(route, 200, { ok: true });
  });
}
