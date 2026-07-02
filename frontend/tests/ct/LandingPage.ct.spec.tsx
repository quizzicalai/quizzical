// frontend/tests/ct/LandingPage.ct.spec.tsx
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { LandingPage } from '../../src/pages/LandingPage';
import { CONFIG_FIXTURE } from '../fixtures/config.fixture';
import './fixtures/config'; // keeps the /config stub, quiz store helpers, and Turnstile stub

test.describe('<LandingPage /> (CT)', () => {
  test.beforeEach(async ({ page }) => {
    // Reset any browser-side state your fixtures maintain
    await page.evaluate(() => window.__ct_resetLastStartQuizCall?.());

    // Visual stability
    await page.emulateMedia({ reducedMotion: 'reduce' });

    // Optional: freeze any loader widgets your app renders
    await page.evaluate(() => {
      (window as any).__FREEZE_LOADERS__ = true;
      document.documentElement.setAttribute('data-freeze-loaders', '');
    });
  });

  async function mountReady(mount: any, page: any) {
    // Mount the page
    await mount(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>
    );

    // T1 (2026-07-02): the current LandingPage gates the FORM on Turnstile
    // token readiness — until a token arrives it renders the "Loading…"
    // preparing block (`lp-preparing`) with the (mocked) Turnstile widget.
    // Resolve the token via the mock button first, THEN the form appears.
    // (The old flow — form first, token demanded on submit — is gone.)
    await expect(page.getByTestId('lp-preparing')).toBeVisible();
    await page.getByTestId('turnstile').click(); // mock resolves 'ct-token'

    const input = page.getByRole('textbox').first();
    await expect(input).toBeVisible();

    // Return common handles
    const submit = page.locator('button[type="submit"]').first();
    return { input, submit };
  }

  test('happy path: requires turnstile, then submits without backend', async ({ mount, page }) => {
    const { input, submit } = await mountReady(mount, page);

    // Button should start disabled until input has value
    await expect(submit).toBeDisabled();

    // Label fallback is "Quiz Topic"; but we avoid a brittle label lookup and use role
    await input.fill('coffee personalities');
    await expect(submit).toBeEnabled();
    await submit.click();

    // Browser-side helper records the last call
    await expect
      .poll(() => page.evaluate(() => window.__ct_lastStartQuizCall ?? null))
      .toEqual({ category: 'coffee personalities', token: 'ct-token' });
  });

  test('error path: category_not_found shows config-driven message', async ({ mount, page }) => {
    const { input, submit } = await mountReady(mount, page);

    await input.fill('unknown');

    // Configure the *browser-side* mock to fail once
    await page.evaluate(() =>
      window.__ct_setNextStartQuizError?.({ code: 'category_not_found', message: 'not found' })
    );
    await submit.click();

    // Inline error should render using fixture text
    await expect(page.getByText(CONFIG_FIXTURE.content.errors.categoryNotFound)).toBeVisible();
  });

  test('submit → shows inline narration until navigation (pending startQuiz)', async ({ mount, page }) => {
    const { input, submit } = await mountReady(mount, page);

    await input.fill('cats');

    // Tell the mock to pause startQuiz so the pending state stays visible
    await page.evaluate(() => {
      window.__ct_setStartQuizPending?.();
    });

    // Submit triggers startQuiz (now pending)
    await submit.click();

    // Inline loader appears inside the same card (no layout jump)
    const strip = page.getByTestId('lp-loading-inline');
    await expect(strip).toBeVisible();

    // T1 (2026-07-02): LoadingNarration no longer reads the window.__ct_*
    // override knobs; the multi-line rotation is covered by the
    // LoadingNarration/QuizFlowPage specs. Here we pin the pending state's
    // first narration line only.
    const text = page.getByTestId('loading-narration-text');
    await expect(text).toHaveText('Thinking…');

    // Release the mock so startQuiz resolves → navigate('/quiz')
    await page.evaluate(() => window.__ct_resolveStartQuizPending?.());

    // startQuiz call recorded
    await expect
      .poll(() => page.evaluate(() => window.__ct_lastStartQuizCall ?? null))
      .toEqual({ category: 'cats', token: 'ct-token' });

    // The inline loader should be gone after navigation/unmount
    await expect(page.getByTestId('lp-loading-inline')).toHaveCount(0);
  });
});
