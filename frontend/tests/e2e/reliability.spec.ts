/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * FE↔BE reliability e2e — Phase 8
 *
 * AC-FE-RELY-POLL-2 / AC-FE-RELY-POLL-3 in a real browser against the bundled
 * production build. We override the polling endpoint to inject transient
 * 503 / 429 responses and assert:
 *   1. The user is NOT shown an error page on a single 5xx — instead the
 *      LoadingCard remains visible and the FE retries, eventually receiving
 *      a 200 and rendering the question.
 *   2. A 429 with Retry-After is honoured (the FE waits ≥ retry-after before
 *      its next request to /quiz/status).
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

test.describe('FE-E2E-RELY: backend transient failures during polling', () => {
  test('FE-E2E-RELY-1: a single 503 on /quiz/status is retried; user sees no error', async ({ page }) => {
    await setupApp(page);

    // Override status to 503 once, then succeed via the default mock.
    let statusCalls = 0;
    await page.route('**/api/v1/quiz/status/**', async (route: Route, request) => {
      statusCalls += 1;
      if (statusCalls === 1) {
        await route.fulfill({
          status: 503,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ detail: 'temporarily unavailable' }),
        });
        return;
      }
      // Fallback to the standard fixture by continuing the request — but
      // installQuizMocks already registered a handler. Calling continue() will
      // bypass it; instead, replicate the standard "active question" payload.
      const url = new URL(request.url());
      const known = Number(url.searchParams.get('known_questions_count') ?? '0');
      if (known === 0) {
        await route.fulfill({
          status: 200,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            status: 'active',
            type: 'question',
            data: {
              type: 'question',
              questionText: 'Reliability question?',
              options: [
                { text: 'A' }, { text: 'B' }, { text: 'C' }, { text: 'D' },
              ],
              imageUrl: null,
            },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ status: 'processing', quiz_id: 'e2e-1' }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Resilience');

    // Synopsis screen → click "Continue/Start" to trigger /quiz/proceed and
    // begin polling.
    await expect(page.getByText(/the world of ancient rome/i)).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: /start|continue|proceed|next/i }).first().click();

    // FE must not show an error page after a single 503 — the question
    // eventually appears once the retry succeeds.
    await expect(page.getByText('Reliability question?')).toBeVisible({ timeout: 30_000 });
    expect(statusCalls).toBeGreaterThanOrEqual(2);
  });

  test('FE-E2E-RELY-2: 429 with Retry-After on /quiz/status is honoured before next request', async ({ page }) => {
    await setupApp(page);

    const callTimes: number[] = [];
    let statusCalls = 0;
    await page.route('**/api/v1/quiz/status/**', async (route: Route, request) => {
      statusCalls += 1;
      callTimes.push(Date.now());
      if (statusCalls === 1) {
        await route.fulfill({
          status: 429,
          headers: {
            'content-type': 'application/json',
            'Retry-After': '2',
          },
          body: JSON.stringify({ detail: 'rate limited', errorCode: 'RATE_LIMITED' }),
        });
        return;
      }
      const url = new URL(request.url());
      const known = Number(url.searchParams.get('known_questions_count') ?? '0');
      if (known === 0) {
        await route.fulfill({
          status: 200,
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            status: 'active',
            type: 'question',
            data: {
              type: 'question',
              questionText: 'After backoff question?',
              options: [
                { text: 'A' }, { text: 'B' }, { text: 'C' }, { text: 'D' },
              ],
              imageUrl: null,
            },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ status: 'processing', quiz_id: 'e2e-1' }),
      });
    });

    await gotoLanding(page);
    await fillCategoryAndSubmit(page, 'Backoff');

    await expect(page.getByText(/the world of ancient rome/i)).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: /start|continue|proceed|next/i }).first().click();

    await expect(page.getByText('After backoff question?')).toBeVisible({ timeout: 30_000 });

    // The next status request must occur ≥ ~2s after the 429.
    expect(callTimes.length).toBeGreaterThanOrEqual(2);
    const gapMs = callTimes[1]! - callTimes[0]!;
    expect(gapMs).toBeGreaterThanOrEqual(1500); // generous slack for jitter / scheduler
  });
});
