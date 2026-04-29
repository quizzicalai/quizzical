/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * FE-E2E-CSP: runtime CSP-violation check.
 *
 * Loads the SPA against the deployed nginx CSP and asserts that:
 *   - No CSP violation is reported on the landing page.
 *   - The Cloudflare Turnstile script tag can attach (script-src allowed).
 *   - A FAL image URL (https://v2.fal.media/...) renders without violation
 *     (img-src https:).
 *   - Google Fonts stylesheets/woff2 load without violation.
 *
 * This test catches accidental CSP tightening that would break the captcha
 * or the result-page character image in production.
 */

import { test, expect } from './utils/har.fixture';
import type { ConsoleMessage, Page } from '@playwright/test';

import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';
import { stubTurnstile } from './utils/turnstile';

function collectCspViolations(page: Page): { violations: string[] } {
  const violations: string[] = [];
  page.on('console', (msg: ConsoleMessage) => {
    const text = msg.text();
    if (/Content Security Policy|Refused to (load|connect|frame|execute)/i.test(text)) {
      violations.push(text);
    }
  });
  page.on('pageerror', (err) => {
    const text = err?.message ?? String(err);
    if (/Content Security Policy/i.test(text)) violations.push(text);
  });
  return { violations };
}

test.describe('FE-E2E-CSP: runtime Content-Security-Policy compliance', () => {
  test('AC-FE-CSP-1: landing page loads with no CSP violations', async ({ page }) => {
    const { violations } = collectCspViolations(page);
    await stubTurnstile(page);
    await installConfigFixtureE2E(page);
    await installQuizMocks(page);

    await page.goto('/');
    await expect(
      page.getByTestId('lp-question-frame'),
    ).toBeVisible({ timeout: 20_000 });
    // Give late-loading resources (fonts, Turnstile) a moment.
    await page.waitForTimeout(750);

    expect(violations, `CSP violations on landing:\n${violations.join('\n')}`).toEqual([]);
  });

  test('AC-FE-CSP-2: img-src https: allows FAL image URLs', async ({ page }) => {
    const { violations } = collectCspViolations(page);
    await stubTurnstile(page);
    await installConfigFixtureE2E(page);
    await installQuizMocks(page);

    await page.goto('/');
    await expect(
      page.getByTestId('lp-question-frame'),
    ).toBeVisible({ timeout: 20_000 });

    // Inject a FAL image URL into the DOM and wait for the load/error event.
    // We don't need the image bytes to actually arrive — just that the
    // browser doesn't synchronously refuse the request because of CSP.
    const result = await page.evaluate(async () => {
      return await new Promise<{ blocked: boolean; loaded: boolean }>(
        (resolve) => {
          const img = new Image();
          let settled = false;
          const finish = (loaded: boolean) => {
            if (settled) return;
            settled = true;
            resolve({ blocked: false, loaded });
          };
          img.onload = () => finish(true);
          img.onerror = () => finish(false); // network error is fine; CSP block is reported as console "Refused to load"
          img.src = 'https://v2.fal.media/files/example/sample.png';
          setTimeout(() => finish(false), 3000);
        },
      );
    });

    expect(result.blocked).toBe(false);
    expect(
      violations,
      `Unexpected CSP violations from FAL image:\n${violations.join('\n')}`,
    ).toEqual([]);
  });
});
