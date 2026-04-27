/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * §9.7.7 — FE↔BE Security Contract (AC-FE-E2E-SEC-1..3).
 *
 * These tests prove the FE never leaks raw BE error detail strings (which
 * could carry stack traces, internal paths, or DB errors) for unenumerated
 * 4xx/5xx responses. They also verify the per-quiz feedback throttle’s 429
 * response is rendered with a friendly message.
 *
 * Pattern matches the existing errorContract.spec.ts: install BE-shaped mock
 * routes over the standard quiz fixtures and assert the user-facing inline
 * error reflects the production-hardening contract.
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
  await expect(
    page
      .getByRole('heading', {
        name: /discover your true personality|unlock your inner persona|create.*quiz/i,
      })
      .first(),
  ).toBeVisible({ timeout: 20_000 });
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

test.describe('§9.7.7 — FE↔BE security contract', () => {
  test('AC-FE-E2E-SEC-1: BE 500 with raw stack-trace detail never reaches the user', async ({ page }) => {
    await setupApp(page);

    const SECRET_DETAIL =
      'Traceback (most recent call last): File "/app/db.py", line 42, in fetch_user NullPointerException: secret_token=ABCDEF12345';

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 500,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ detail: SECRET_DETAIL }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Ancient Rome');

    // Stays on landing.
    await expect(page).toHaveURL(/\/$/);

    // The friendly error appears…
    const inlineError = page
      .locator('.text-red-600, .text-red-500, [role="alert"]')
      .first();
    await expect(inlineError).toBeVisible({ timeout: 10_000 });

    // …and the raw BE detail (with file paths, exception names, secret token)
    // is NOT present anywhere in the rendered page.
    const bodyText = (await page.locator('body').innerText()) || '';
    expect(bodyText).not.toContain('Traceback');
    expect(bodyText).not.toContain('NullPointerException');
    expect(bodyText).not.toContain('secret_token');
    expect(bodyText).not.toContain('/app/db.py');
  });

  test('AC-FE-E2E-SEC-2: BE 400 with unenumerated errorCode -> friendly message, raw detail hidden', async ({
    page,
  }) => {
    await setupApp(page);

    const RAW_DETAIL =
      'sqlalchemy.exc.IntegrityError: duplicate key value violates unique constraint "users_email_key"';

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 400,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          detail: RAW_DETAIL,
          errorCode: 'NEVER_SEEN_BEFORE',
        }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'World History');

    await expect(page).toHaveURL(/\/$/);
    const inlineError = page
      .locator('.text-red-600, .text-red-500, [role="alert"]')
      .first();
    await expect(inlineError).toBeVisible({ timeout: 10_000 });

    const bodyText = (await page.locator('body').innerText()) || '';
    expect(bodyText).not.toContain('sqlalchemy');
    expect(bodyText).not.toContain('IntegrityError');
    expect(bodyText).not.toContain('users_email_key');
  });

  test('AC-FE-E2E-SEC-3: BE 429 RATE_LIMITED for /quiz/start surfaces friendly throttle message', async ({
    page,
  }) => {
    await setupApp(page);

    await page.route('**/api/v1/quiz/start', async (route: Route) => {
      await route.fulfill({
        status: 429,
        headers: {
          'content-type': 'application/json',
          'Retry-After': '7',
        },
        body: JSON.stringify({
          detail: 'Too many requests. Please slow down.',
          errorCode: 'RATE_LIMITED',
        }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Greek Mythology');

    await expect(page).toHaveURL(/\/$/);
    const inlineError = page
      .locator('.text-red-600, .text-red-500, [role="alert"]')
      .first();
    await expect(inlineError).toBeVisible({ timeout: 10_000 });
    await expect(inlineError).toContainText(/too many|please try again|could not create/i);
  });
});
