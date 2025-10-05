import type { Page, Route } from '@playwright/test';

const BASE = '/api/v1';

export async function installQuizMocks(page: Page) {
  // /quiz/start → initial synopsis
  await page.route(`${BASE}/quiz/start`, async (route: Route) => {
    const json = {
      quizId: 'e2e-1',
      initialPayload: {
        type: 'synopsis',
        data: {
          // IMPORTANT: inner object also carries type per Zod schema
          type: 'synopsis',
          title: 'The World of Ancient Rome',
          summary: 'A short intro...',
          // backend may include imageUrl; harmless if present
          imageUrl: '',
        },
      },
    };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
  });

  // /quiz/proceed → processing
  await page.route(`${BASE}/quiz/proceed`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'processing', quiz_id: 'e2e-1' }),
    });
  });

  // /quiz/status/:id → first question, then final result
  let answered = false;
  await page.route(new RegExp(`${BASE}/quiz/status/e2e-1.*`), async (route: Route) => {
    const response = answered
      ? {
          status: 'finished',
          type: 'result',
          data: {
            // IMPORTANT: use { title, description, imageUrl? }
            title: 'The Architect',
            description: 'You are…',
            imageUrl: '',
          },
        }
      : {
          status: 'active',
          type: 'question',
          data: {
            // IMPORTANT: API uses `options`; UI turns them into answers
            text: 'Which achievement is most impressive?',
            options: [{ text: 'Aqueducts' }, { text: 'Roads' }],
          },
        };

    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) });
  });

  // /quiz/next → flip answered=true
  await page.route(`${BASE}/quiz/next`, async (route: Route) => {
    answered = true;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) });
  });
}
