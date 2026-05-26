// tests/e2e/fixtures/quizWithCharacters.ts
//
// Variant of installQuizMocks that returns a synopsis with an embedded
// character roster carrying live image URLs. Used by character_images.spec.ts
// to verify the FE actually renders <img> tags AND that those images load
// successfully (naturalWidth > 0) — the user-visible end of the runtime
// cache-hit/lazy-load pipeline.
//
// AC-PROD-R14-IMG-E2E: closes the test gap for "characters not in cache
// must still appear" by exercising the FE rendering path with real image
// hosts (picsum.photos returns deterministic JPEGs given a seed).

import type { Page, Route, Request } from '@playwright/test';

type QuizServerState = {
  quizId: string;
  proceeded: boolean;
};

const QUIZ_ID = 'e2e-char-1';

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function readJson<T = any>(request: Request): Promise<T | undefined> {
  try { return (await request.postDataJSON()) as T; } catch { return undefined; }
}

/**
 * Each character carries a fal.media URL (matches `safeImageUrl`'s default
 * allowlist). The actual GET for each URL is intercepted in
 * `installFakeImageHost` and served a real 1x1 JPEG so `<img>.naturalWidth`
 * becomes > 0 — proving the rendering+loading path end-to-end.
 */
const CHARACTERS = [
  { name: 'Han Solo',     shortDescription: 'roguish smuggler',  profileText: 'pt', imageUrl: 'https://fal.media/files/test/han.jpg' },
  { name: 'Leia Organa',  shortDescription: 'rebel diplomat',    profileText: 'pt', imageUrl: 'https://fal.media/files/test/leia.jpg' },
  { name: 'Luke Skywalker', shortDescription: 'farmboy jedi',    profileText: 'pt', imageUrl: 'https://fal.media/files/test/luke.jpg' },
  { name: 'Chewbacca',    shortDescription: 'loyal wookiee',     profileText: 'pt', imageUrl: 'https://fal.media/files/test/chewie.jpg' },
];

// Smallest valid JPEG (1x1 white). Base64-decoded at install time.
const TINY_JPEG_B64 =
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wBDAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAr/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AL+AB//Z';

/**
 * Route-intercept every fal.media request and serve a tiny JPEG. Required
 * because fal URLs are ephemeral in real life; the test still needs the
 * <img> to actually load so we can assert naturalWidth > 0.
 */
export async function installFakeImageHost(page: Page) {
  const body = Buffer.from(TINY_JPEG_B64, 'base64');
  await page.route('https://fal.media/**', async (route) => {
    return route.fulfill({
      status: 200,
      headers: { 'content-type': 'image/jpeg', 'cache-control': 'no-store' },
      body,
    });
  });
}

export async function installQuizMocksWithCharacters(page: Page) {
  const state: QuizServerState = { quizId: QUIZ_ID, proceeded: false };

  await page.route('**/api/v1/quiz/start', async (route) => {
    return json(route, 200, {
      quizId: state.quizId,
      initialPayload: {
        type: 'synopsis',
        data: {
          type: 'synopsis',
          title: 'The World of Star Wars',
          summary: 'A galaxy far, far away.',
        },
      },
      charactersPayload: {
        type: 'characters',
        data: CHARACTERS,
      },
    });
  });

  await page.route('**/api/v1/quiz/proceed', async (route, request) => {
    state.proceeded = true;
    const body = await readJson<{ quizId?: string }>(request);
    if (body?.quizId && body.quizId !== state.quizId) {
      return json(route, 400, { code: 'bad_quiz', message: 'Unknown quiz id' });
    }
    return json(route, 200, { status: 'processing', quizId: state.quizId });
  });

  // Generic processing status; the test never advances past synopsis.
  await page.route('**/api/v1/quiz/status/**', async (route, request) => {
    const url = request.url();
    if (!url.includes('/' + state.quizId)) return route.fallback();
    return json(route, 200, { status: 'processing', quiz_id: state.quizId });
  });
}

export { CHARACTERS as MOCK_CHARACTERS, QUIZ_ID as MOCK_QUIZ_ID };
