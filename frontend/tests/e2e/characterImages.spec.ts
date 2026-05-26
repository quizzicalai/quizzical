/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
//
// AC-PROD-R14-IMG-E2E — guarantees the user sees a character image for
// every character in the synopsis roster. Closes the test gap behind
// objective #6 ("our strategy is well tested both locally and in the
// CI/CD actions").
//
// What this asserts:
//   1. /start returns a synopsis with a 4-character roster (Star Wars-shaped).
//   2. The FE renders an <img> for each character.
//   3. Each <img> actually loads (naturalWidth > 0). This catches:
//      - safeImageUrl allowlist regressions (would yield no <img> at all)
//      - silent 404s / wrong content-type from the cache layer
//      - frontend rendering bugs where characters appear without images
//
// This test sits alongside the backend unit tests in
// tests/unit/services/test_image_pipeline.py (cache-hit + dead-URL paths)
// and tests/unit/api/test_quiz_schedule_image_jobs.py (brand routing).

import { test, expect } from './utils/har.fixture';
import type { Page, Response as PWResponse } from '@playwright/test';

import { installConfigFixtureE2E } from './fixtures/config';
import {
  installQuizMocksWithCharacters,
  installFakeImageHost,
  MOCK_CHARACTERS,
} from './fixtures/quizWithCharacters';
import { stubTurnstile } from './utils/turnstile';

async function setup(page: Page) {
  await stubTurnstile(page);
  await installConfigFixtureE2E(page);
  await installFakeImageHost(page); // must precede quiz mocks (order matters for /api routes is fine)
  await installQuizMocksWithCharacters(page);
}

test.describe('character images on synopsis', () => {
  test('renders an <img> for every character and each one loads', async ({ page }) => {
    await setup(page);

    await Promise.all([
      page.waitForResponse(
        (r: PWResponse) => r.url().includes('/api/v1/config') && r.ok(),
        { timeout: 30_000 },
      ),
      page.goto('/'),
    ]);

    // Type a category and start.
    const input = page.getByRole('textbox').first();
    await input.fill('Star Wars characters');

    const startRespPromise = page.waitForResponse(
      (r: PWResponse) => r.url().includes('/api/v1/quiz/start') && r.ok(),
      { timeout: 15_000 },
    );
    const createBtn = page.getByRole('button', { name: /start quiz|create my quiz/i });
    await createBtn.click();
    await startRespPromise;

    // Synopsis appears.
    await expect(
      page.getByText(/the world of star wars/i),
    ).toBeVisible({ timeout: 15_000 });

    // Roster list is present with the correct count.
    const roster = page.getByRole('list', { name: /generated characters/i });
    await expect(roster).toBeVisible();
    const items = roster.getByRole('listitem');
    await expect(items).toHaveCount(MOCK_CHARACTERS.length);

    // Every roster item has an <img> AND it actually loaded.
    const imgs = roster.locator('img');
    await expect(imgs).toHaveCount(MOCK_CHARACTERS.length);

    // Wait for all images to finish loading (naturalWidth becomes > 0).
    await expect.poll(
      async () =>
        await imgs.evaluateAll((nodes) =>
          (nodes as HTMLImageElement[]).every((n) => n.complete && n.naturalWidth > 0),
        ),
      { timeout: 10_000, message: 'every character <img> must load' },
    ).toBe(true);
  });
});
