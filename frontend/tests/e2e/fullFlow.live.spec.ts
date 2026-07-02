import { test, expect, type Page } from './utils/har.fixture';
import { stubTurnstile } from './utils/turnstile';

/**
 * FULL-FLOW LIVE e2e (owner request): drive the REAL backend agent from the
 * very beginning (topic generation) to the very end (share → feedback →
 * restart) WITHOUT interruption and WITHOUT API mocks. Costly (real LLM/FAL),
 * so it is MANUALLY TRIGGERED — gated behind RUN_LIVE_E2E=1 (skipped in CI).
 *
 * Run:
 *   RUN_LIVE_E2E=1 npx playwright test fullFlow.live --project=chromium
 * (against a stack with a real backend, e.g. VITE against the deployed API or a
 * local docker stack. Turnstile is stubbed so the widget resolves in-test.)
 *
 * Three scenarios span the buckets: a canonical topic (fast precompute path),
 * an open historical topic, and a whimsical/creative topic.
 */

const RUN_LIVE_E2E = process.env.RUN_LIVE_E2E === '1';

const TOPICS = ['Hogwarts House', 'Ancient Rome', 'Type of Coffee'];

/** Answer questions one at a time until the result (share bar) appears. */
async function answerUntilResult(page: Page): Promise<number> {
  const shareBar = page.getByTestId('social-share-bar');
  const answers = page.locator('button[aria-label*="Select answer"]');
  for (let i = 0; i < 40; i++) {
    if (await shareBar.isVisible().catch(() => false)) return i;
    // Wait for whichever comes first: the next question or the result.
    await Promise.race([
      answers.first().waitFor({ state: 'visible', timeout: 90_000 }).catch(() => {}),
      shareBar.waitFor({ state: 'visible', timeout: 90_000 }).catch(() => {}),
    ]);
    if (await shareBar.isVisible().catch(() => false)) return i;
    if (await answers.first().isVisible().catch(() => false)) {
      await answers.first().click();
      await page.waitForTimeout(400); // let the agent advance to the next step
    }
  }
  throw new Error('Did not reach the result page within 40 answers');
}

test.describe('LIVE-E2E full agent flow (topic → share → feedback → restart, non-mocked)', () => {
  test.skip(!RUN_LIVE_E2E, 'Set RUN_LIVE_E2E=1 to run the costly full-flow live agent test.');

  for (const topic of TOPICS) {
    test(`completes the whole quiz for "${topic}"`, async ({ page }) => {
      test.setTimeout(360_000); // real LLM + image jobs are slow
      await stubTurnstile(page);

      // 1. Topic generation
      await page.goto('/');
      await expect(page.getByTestId('lp-question-frame')).toBeVisible({ timeout: 30_000 });
      await page.getByRole('textbox').first().fill(topic);
      await page.getByRole('button', { name: /start quiz|create my quiz|generate quiz/i }).first().click();

      // 2. Synopsis → proceed
      const proceed = page.getByRole('button', { name: /begin|start.*quiz|continue|proceed/i }).first();
      await expect(proceed).toBeVisible({ timeout: 60_000 });
      await proceed.click();

      // 3. Answer every question through to the result
      await answerUntilResult(page);

      // 4. Result reached — share tray present + openable
      await expect(page.getByTestId('social-share-bar')).toBeVisible({ timeout: 120_000 });
      const shareTrigger = page.getByTestId('social-share-trigger');
      if (await shareTrigger.isVisible().catch(() => false)) {
        await shareTrigger.click();
        await expect(page.getByTestId('social-share-modal')).toBeVisible();
        await page.keyboard.press('Escape');
      }

      // 5. Feedback — rate + comment (comment is required) + submit
      const feedbackUp = page.getByTestId('feedback-up');
      if (await feedbackUp.isVisible().catch(() => false)) {
        await feedbackUp.click();
        const comment = page.locator('#feedback-comment');
        await expect(comment).toBeVisible();
        await comment.fill('Great quiz — automated full-flow e2e.');
        const submit = page.getByTestId('feedback-submit');
        await expect(submit).toBeEnabled({ timeout: 10_000 });
        await submit.click();
      }

      // 6. Restart — "Start Another Quiz" returns to a fresh landing
      const restart = page.getByTestId('final-start-another');
      await expect(restart).toBeVisible({ timeout: 15_000 });
      await restart.click();
      await expect(page.getByTestId('lp-question-frame')).toBeVisible({ timeout: 30_000 });
    });
  }
});
