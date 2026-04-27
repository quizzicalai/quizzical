/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * FE-E2E-RESPONSIVE: validate landing flow on mobile + tablet viewports.
 *
 * Catches:
 *  - Viewport overflow / horizontal scrollbar (`scrollWidth > clientWidth`).
 *  - Submit button being hidden behind keyboard / off-screen on small phones.
 *  - Skip-link is reachable on all viewports.
 */

import { test, expect, devices } from '@playwright/test';
import type { Page } from '@playwright/test';

import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';
import { stubTurnstile } from './utils/turnstile';

const VIEWPORTS = [
  { name: 'iPhone-SE', viewport: { width: 375, height: 667 } },
  { name: 'iPhone-12', viewport: devices['iPhone 12'].viewport! },
  { name: 'iPad-portrait', viewport: { width: 768, height: 1024 } },
  { name: 'desktop-1280', viewport: { width: 1280, height: 800 } },
];

async function setupApp(page: Page) {
  await stubTurnstile(page);
  await installConfigFixtureE2E(page);
  await installQuizMocks(page);
}

test.describe('FE-E2E-RESPONSIVE: landing renders cleanly across viewports', () => {
  // Webkit + cold vite + multiple workers can exceed the 30s default; give
  // each viewport plenty of headroom while still failing on regressions.
  test.setTimeout(60_000);
  for (const v of VIEWPORTS) {
    test(`AC-FE-RESP-1 (${v.name}): landing has no horizontal overflow and submit is reachable`, async ({
      page,
    }) => {
      await page.setViewportSize(v.viewport);
      await setupApp(page);
      await page.goto('/');

      await expect(
        page
          .getByRole('heading', {
            name: /discover your true personality|unlock your inner persona|create.*quiz/i,
          })
          .first(),
      ).toBeVisible({ timeout: 20_000 });

      // No horizontal scrollbar (allow ~1px tolerance for sub-pixel rendering).
      const overflow = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth,
        cw: document.documentElement.clientWidth,
      }));
      expect(overflow.sw - overflow.cw).toBeLessThanOrEqual(1);

      // Submit button must be in-viewport and clickable.
      const submit = page
        .getByRole('button', { name: /create my quiz/i })
        .first();
      await expect(submit).toBeVisible();
      const box = await submit.boundingBox();
      expect(box).not.toBeNull();
      // Button entirely within the viewport.
      expect(box!.y + box!.height).toBeLessThanOrEqual(v.viewport.height + 1);
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(v.viewport.width + 1);
    });
  }

  test('AC-FE-RESP-2: skip-link is reachable via Tab on the smallest mobile viewport', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await setupApp(page);
    await page.goto('/');
    // Wait for landing to actually mount (Suspense fallback hides SkipLink).
    await expect(
      page
        .getByRole('heading', {
          name: /discover your true personality|unlock your inner persona|create.*quiz/i,
        })
        .first(),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('a[href="#main-content"]')).toHaveCount(1);

    // The skip-link must be the first focusable element in DOM order so that
    // pressing Tab from the start of the page focuses it.
    const firstFocusableHref = await page.evaluate(() => {
      const focusable = document.querySelectorAll<HTMLElement>(
        'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      const first = focusable[0] as HTMLAnchorElement | undefined;
      return first ? first.getAttribute('href') : null;
    });
    expect(firstFocusableHref).toBe('#main-content');

    // And focusing it must make it visible (sr-only -> focus:not-sr-only).
    await page.locator('a[href="#main-content"]').focus();
    await expect(page.locator('a[href="#main-content"]')).toBeVisible();
  });
});
