// frontend/tests/ct/LandingPage.ct.spec.tsx
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { LandingPage } from '../../src/pages/LandingPage';
import { CONFIG_FIXTURE } from '../fixtures/config.fixture';
import './fixtures/config'; // keeps the /config route stub active

test.describe('<LandingPage /> (CT)', () => {
  test.beforeEach(async ({ page }) => {
    // Clean the in-browser mock state before each test
    await page.evaluate(() => window.__ct_resetLastStartQuizCall?.());
    // Default to reduced motion for visual stability
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });

  test('happy path: requires turnstile, then submits without backend', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>
    );

    const submit = page.getByRole('button', {
      name: new RegExp(CONFIG_FIXTURE.content.landingPage.submitButton, 'i'),
    });
    await expect(submit).toBeDisabled();

    await page.getByLabel(/quiz (category )?input|quiz topic/i).fill('coffee personalities');

    // First submit demands Turnstile
    await submit.click();
    await expect(page.getByText(/please complete the security verification/i)).toBeVisible();

    // Satisfy Turnstile via mock, then submit
    await page.getByTestId('turnstile').click();
    await submit.click();

    // Read the last call from the browser context (not from Node)
    await expect.poll(() =>
      page.evaluate(() => window.__ct_lastStartQuizCall ?? null)
    ).toEqual({ category: 'coffee personalities', token: 'ct-token' });
  });

  test('error path: category_not_found shows config-driven message', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>
    );

    const input = page.getByLabel(/quiz (category )?input|quiz topic/i);
    const submit = page.getByRole('button', {
      name: new RegExp(CONFIG_FIXTURE.content.landingPage.submitButton, 'i'),
    });

    await input.fill('unknown');

    // Require Turnstile first
    await submit.click();
    await page.getByTestId('turnstile').click();

    // Configure the *browser-side* mock to fail once with code=category_not_found
    await page.evaluate(() =>
      window.__ct_setNextStartQuizError?.({ code: 'category_not_found', message: 'not found' })
    );
    await submit.click();

    // Inline error should render using fixture text
    await expect(page.getByText(CONFIG_FIXTURE.content.errors.categoryNotFound)).toBeVisible();
  });

  test('submit → shows inline narration until navigation (pending startQuiz)', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>
    );

    const input = page.getByLabel(/quiz (category )?input|quiz topic/i);
    const submit = page.getByRole('button', {
      name: new RegExp(CONFIG_FIXTURE.content.landingPage.submitButton, 'i'),
    });

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

    // Narration ticks while pending (sprite is independent/reduced)
    const text = page.getByTestId('loading-narration-text');
    await expect(text).toHaveText('Thinking…');
    await page.waitForTimeout(80);
    await expect(text).toHaveText('Researching topic…');
    await page.waitForTimeout(80);
    await expect(text).toHaveText('Determining characters…');

    // Release the mock so startQuiz resolves → navigate('/quiz')
    await page.evaluate(() => window.__ct_resolveStartQuizPending?.());

    // startQuiz call recorded
    await expect.poll(() =>
      page.evaluate(() => window.__ct_lastStartQuizCall ?? null)
    ).toEqual({ category: 'cats', token: 'ct-token' });

    // The inline loader should be gone after navigation/unmount
    await expect(page.getByTestId('lp-loading-inline')).toHaveCount(0);
  });
});
