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

    // Wait until config-driven UI is present.
    // The LandingPage returns <Spinner /> until config is non-null,
    // so wait for the text box (or the form) to appear.
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

    // First submit demands Turnstile
    await submit.click();
    await expect(page.getByText(/please complete the security verification/i)).toBeVisible();

    // Satisfy Turnstile via the test stub, then submit again
    await page.getByTestId('turnstile').click(); // your stub sets a token
    await submit.click();

    // Browser-side helper records the last call
    await expect
      .poll(() => page.evaluate(() => window.__ct_lastStartQuizCall ?? null))
      .toEqual({ category: 'coffee personalities', token: 'ct-token' });
  });

  test('error path: category_not_found shows config-driven message', async ({ mount, page }) => {
    const { input, submit } = await mountReady(mount, page);

    await input.fill('unknown');

    // Require Turnstile first
    await submit.click();
    await page.getByTestId('turnstile').click();

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

    // First submit → require Turnstile
    await submit.click();
    await page.getByTestId('turnstile').click();

    // Tell the mock to pause startQuiz, and speed up narration for the test
    await page.evaluate(() => {
      window.__ct_setStartQuizPending?.();
      window.__ct_loadingLines = [
        { atMs: 0,   text: 'Thinking…' },
        { atMs: 60,  text: 'Researching topic…' },
        { atMs: 120, text: 'Determining characters…' },
      ];
      window.__ct_loadingTickMs = 10;
    });

    // Second submit actually triggers startQuiz (now pending)
    await submit.click();

    // Inline loader appears inside the same card (no layout jump)
    const strip = page.getByTestId('lp-loading-inline');
    await expect(strip).toBeVisible();

    // Narration ticks while pending
    const text = page.getByTestId('loading-narration-text');
    await expect(text).toHaveText('Thinking…');
    await page.waitForTimeout(80);
    await expect(text).toHaveText('Researching topic…');
    await page.waitForTimeout(80);
    await expect(text).toHaveText('Determining characters…');

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
