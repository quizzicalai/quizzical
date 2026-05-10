import { test, expect } from './utils/har.fixture';
import { stubTurnstile } from './utils/turnstile';

const RUN_LIVE_E2E = process.env.RUN_LIVE_E2E === '1';

test.describe('LIVE-E2E result share flow (non-mocked)', () => {
  test.skip(!RUN_LIVE_E2E, 'Set RUN_LIVE_E2E=1 to run live non-mocked flow against real backend');

  test('reaches result page and exposes social share tray without API route mocks', async ({ page }) => {
    await stubTurnstile(page);

    await page.goto('/');
    await expect(page.getByTestId('lp-question-frame')).toBeVisible({ timeout: 30_000 });

    await page.getByRole('textbox').first().fill('Ancient Rome');
    await page
      .getByRole('button', { name: /start quiz|create my quiz|generate quiz/i })
      .first()
      .click();

    // Synopsis screen
    await expect(
      page.getByRole('button', { name: /begin|start.*quiz|continue|proceed/i }).first(),
    ).toBeVisible({ timeout: 45_000 });
    await page
      .getByRole('button', { name: /begin|start.*quiz|continue|proceed/i })
      .first()
      .click();

    // First adaptive question + answer
    const answerButtons = page.locator('button[aria-label*="Select answer"]');
    await expect(answerButtons.first()).toBeVisible({ timeout: 45_000 });
    await answerButtons.first().click();

    // Result page can take time with real LLM/image background jobs.
    await expect(page.getByTestId('social-share-bar')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('social-share-preview')).toBeVisible();
    await expect(page.getByTestId('social-share-copy')).toBeVisible();
    await expect(page.getByTestId('social-share-x')).toBeVisible();
  });
});
