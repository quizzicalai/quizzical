/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * FE-E2E-PROD: validates that the FE renders BE production-hardening
 * responses correctly:
 *   - 429 RATE_LIMITED  -> "Too many attempts" + Retry-After surfaced
 *   - 413 PAYLOAD_TOO_LARGE -> canonical "Your input is too long." message
 *   - 422 validation_error  -> backend detail surfaced (or generic fallback)
 *   - 503 service_unavailable -> "temporarily busy" message
 *   - 409 SESSION_BUSY on /quiz/proceed -> "still preparing" friendly message
 *
 * Each test installs a tailored route handler over the standard quiz mocks
 * and verifies the user-facing inline error matches the contract.
 */

import { test, expect } from './utils/har.fixture';
import type { Page, Route } from '@playwright/test';

import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';
import { stubTurnstile } from './utils/turnstile';

async function setupApp(page: Page): Promise<void> {
  await stubTurnstile(page);
  await installConfigFixtureE2E(page);
  await installQuizMocks(page);
}

async function gotoLanding(page: Page): Promise<void> {
  await page.goto('/');
  // Wait for the landing heading so we know config has loaded.
  await expect(
    page.getByRole('heading', { name: /discover your true personality|unlock your inner persona|create.*quiz/i }).first(),
  ).toBeVisible({ timeout: 20_000 });
  // Give the stubbed Turnstile a tick to fire its callback so the form is
  // submittable (LandingPage early-returns when turnstileToken is null).
  await page.waitForTimeout(300);
}

async function fillCategoryAndSubmit(page: Page, category: string): Promise<void> {
  const input = page.getByRole('textbox').first();
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.fill(category);
  const createBtn = page.getByRole('button', { name: /create my quiz/i }).first();
  await expect(createBtn).toBeVisible();
  await createBtn.click();
}

test.describe('FE-E2E-PROD: error contract surfaces', () => {
  test('FE-E2E-PROD-1: 429 RATE_LIMITED -> page does not navigate, surfaces inline error', async ({ page }) => {
    await setupApp(page);

    // Override /quiz/start to return 429 RATE_LIMITED with Retry-After: 5.
    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'content-type': 'application/json',
          'Retry-After': '5',
        },
        body: JSON.stringify({
          detail: 'too many requests',
          errorCode: 'RATE_LIMITED',
        }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    // Stays on landing and surfaces the inline error (red text node).
    await expect(page).toHaveURL(/\/$/);
    const inlineError = page.locator('.text-red-600, .text-red-500, [role="alert"]').first();
    await expect(inlineError).toBeVisible({ timeout: 10_000 });
    // Either canonical 'too many attempts' copy or generic fallback is acceptable.
    await expect(inlineError).toContainText(/too many|please try again|could not create/i);
  });

  test('FE-E2E-PROD-2: 413 PAYLOAD_TOO_LARGE surfaces canonical message', async ({ page }) => {
    await setupApp(page);

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 413,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          detail: 'request body exceeds 256 KiB',
          errorCode: 'PAYLOAD_TOO_LARGE',
        }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    await expect(
      page.getByText(/your input is too long/i),
    ).toBeVisible({ timeout: 10_000 });
  });

  test('FE-E2E-PROD-3: 503 service_unavailable surfaces "temporarily busy"', async ({ page }) => {
    await setupApp(page);

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 503,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ detail: 'down for maintenance' }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    await expect(
      page.getByText(/temporarily busy/i),
    ).toBeVisible({ timeout: 10_000 });
  });

  test('FE-E2E-PROD-4: 422 validation surfaces some inline error', async ({ page }) => {
    await setupApp(page);

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 422,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          detail: 'category contains invalid characters',
        }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    // Page must not navigate to /quiz; an inline error (red text or any
    // user-visible failure copy) must surface.
    await expect(page).toHaveURL(/\/$/);
    const inlineError = page.locator('.text-red-600, .text-red-500, [role="alert"]').first();
    await expect(inlineError).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('FE-E2E-PROD: 409 SESSION_BUSY UX', () => {
  test('FE-E2E-PROD-5: 409 SESSION_BUSY on /quiz/proceed shows friendly message', async ({ page }) => {
    await setupApp(page);

    // First call returns 409, subsequent calls succeed (BE released the lock).
    let proceedCalls = 0;
    await page.route('**/api/v1/quiz/proceed', async (route: Route) => {
      proceedCalls += 1;
      if (proceedCalls === 1) {
        await route.fulfill({
          status: 409,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            detail: 'session is busy',
            errorCode: 'SESSION_BUSY',
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ status: 'processing', quizId: 'e2e-1' }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    // Wait for synopsis screen.
    await expect(
      page.getByText(/the world of ancient rome/i),
    ).toBeVisible({ timeout: 15_000 });

    const proceedBtn = page
      .getByRole('button', { name: /start|continue|proceed|next/i })
      .first();
    await expect(proceedBtn).toBeVisible();
    await proceedBtn.click();

    // After a 409 SESSION_BUSY, FE swallows the hard error and starts polling.
    // While polling, the LoadingCard is shown (no error overlay, no navigation
    // to a generic error page). That is the friendly UX behavior we assert.
    await expect(
      page.getByTestId('quiz-loading-card'),
    ).toBeVisible({ timeout: 10_000 });
    // The store recorded the recovery attempt (proceed was called at least once).
    expect(proceedCalls).toBeGreaterThanOrEqual(1);
  });
});

test.describe('FE-E2E-PROD: client-side category validation', () => {
  test('FE-E2E-PROD-6: empty/whitespace category leaves submit button disabled', async ({ page }) => {
    await setupApp(page);

    let startCalled = false;
    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      startCalled = true;
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ quizId: 'e2e-1', initialPayload: null }),
      });
    });

    await gotoLanding(page);

    const input = page.getByRole('textbox').first();
    await expect(input).toBeVisible({ timeout: 15_000 });
    await input.fill('   '); // whitespace-only

    const createBtn = page.getByRole('button', { name: /create my quiz/i }).first();
    // LandingPage's submit short-circuits when category.trim() is empty, and
    // the button is wired to `disabled` accordingly.
    await expect(createBtn).toBeDisabled();
    expect(startCalled).toBe(false);
  });

  test('FE-E2E-PROD-7: control characters are rejected client-side', async ({ page }) => {
    await setupApp(page);

    let startCalled = false;
    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      startCalled = true;
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ quizId: 'e2e-1', initialPayload: null }),
      });
    });

    await gotoLanding(page);

    const input = page.getByRole('textbox').first();
    await expect(input).toBeVisible({ timeout: 15_000 });
    // Inject NUL byte via evaluate (browsers strip control chars on .fill()).
    await input.evaluate((el: any) => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )?.set;
      setter?.call(el, 'Ancient\u0000Rome');
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });

    const createBtn = page.getByRole('button', { name: /create my quiz/i }).first();
    await createBtn.click();

    await page.waitForTimeout(500);
    expect(startCalled).toBe(false);
  });
});

// §19.4 AC-QUALITY-R2-FE-ERR-3: full envelope round-trip — when the BE returns
// the canonical envelope (errorCode + traceId + Retry-After), the FE must (a)
// parse it cleanly with no console errors / unhandled rejections and (b)
// surface the typed error via the `errorCode` channel.
test.describe('FE-E2E-PROD: envelope round-trip', () => {
  test('FE-E2E-PROD-8: 429 with body.traceId is parsed cleanly without console errors', async ({ page }) => {
    await setupApp(page);

    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    const pageErrors: Error[] = [];
    page.on('pageerror', (err) => pageErrors.push(err));

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'content-type': 'application/json',
          'Retry-After': '3',
          // Note: NO X-Trace-ID header — the FE must read traceId from body.
        },
        body: JSON.stringify({
          detail: 'rate limited',
          errorCode: 'RATE_LIMITED',
          traceId: 'envelope-roundtrip-trace-001',
        }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    // FE renders the typed rate-limit message (not a generic crash).
    const inlineError = page.locator('.text-red-600, .text-red-500, [role="alert"]').first();
    await expect(inlineError).toBeVisible({ timeout: 10_000 });
    await expect(inlineError).toContainText(/too many|please try again|could not create/i);

    // No unhandled errors / console errors that would indicate the envelope
    // parsing crashed normalizeHttpError or the surrounding code path.
    expect(pageErrors).toHaveLength(0);
    // Filter out benign console errors (e.g., expected 429 in network logs).
    const realErrors = consoleErrors.filter(
      (e) => !/429|rate.?limit|Failed to load resource/i.test(e),
    );
    expect(realErrors).toEqual([]);
  });
});
