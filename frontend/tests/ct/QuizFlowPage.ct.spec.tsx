import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QuizFlowPage } from '../../src/pages/QuizFlowPage';
import './fixtures/config'; // same config mock/stub as other CT

test.describe('<QuizFlowPage /> (CT)', () => {
  test.beforeEach(async ({ page }) => {
    // Deterministic visuals
    await page.emulateMedia({ reducedMotion: 'reduce' });

    // Start in idle+polling to hit the LoadingCard path
    await page.evaluate(() => {
      window.__ct_quiz_reset?.();
      window.__ct_quiz_set?.({ quizId: 'ct-quiz-1', currentView: 'idle', isPolling: true });

      // Speed up narration for CT
      window.__ct_loadingLines = [
        { atMs: 0,  text: 'Thinking…' },
        { atMs: 80, text: 'Researching topic…' },
        { atMs: 160, text: 'Determining characters…' },
      ];
      window.__ct_loadingTickMs = 10;
    });
  });

  test('shows LoadingCard while processing and narration ticks', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );

    const container = page.getByTestId('quiz-loading-card');
    await expect(container).toBeVisible();

    const text = page.getByTestId('loading-narration-text');
    await expect(text).toHaveText('Thinking…');
    await page.waitForTimeout(90);
    await expect(text).toHaveText('Researching topic…');
    await page.waitForTimeout(90);
    await expect(text).toHaveText('Determining characters…');
  });

  test('stops immediately when backend flips to synopsis', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );

    // Flip the store to "synopsis"
    await page.waitForTimeout(100);
    await page.evaluate(() => {
      window.__ct_quiz_set?.({
        currentView: 'synopsis',
        isPolling: false,
        viewData: { title: 'Cats vs Dogs', summary: 'The eternal rivalry.' },
      });
    });

    // Wait for render: loader gone, new .lp-card present
    await page.waitForFunction(() => {
      const loading = document.querySelector('[data-testid="quiz-loading-card"]');
      const card = document.querySelector('.lp-card');
      return !loading && !!card;
    });

    // Expect synopsis content visible (rendered by real SynopsisView)
    await expect(page.getByText('Cats vs Dogs')).toBeVisible();
    await expect(page.getByText('The eternal rivalry.')).toBeVisible();
  });

  test('no big CLS between loading and synopsis', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );

    // Measure lp-card (inside LoadingCard)
    const h1 = await page.evaluate(() => {
      const card = document.querySelector('.lp-card') as HTMLElement | null;
      return card ? Math.round(card.getBoundingClientRect().height) : 0;
    });

    // Flip to synopsis
    await page.evaluate(() => {
      window.__ct_quiz_set?.({
        currentView: 'synopsis',
        isPolling: false,
        viewData: { title: 'Cats vs Dogs', summary: 'The eternal rivalry.' },
      });
    });

    // Ensure the new card is rendered before measuring
    await page.waitForFunction(() => {
      const loading = document.querySelector('[data-testid="quiz-loading-card"]');
      const card = document.querySelector('.lp-card');
      return !loading && !!card;
    });

    const h2 = await page.evaluate(() => {
      const card = document.querySelector('.lp-card') as HTMLElement | null;
      return card ? Math.round(card.getBoundingClientRect().height) : 0;
    });

    const diff = Math.abs(h1 - h2);
    expect(diff).toBeLessThanOrEqual(2);
  });

  test('reduced motion snapshot of loading state', async ({ mount, page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );

    const container = page.getByTestId('quiz-loading-card'); // snapshot a visible, stable element
    await expect(container).toBeVisible();
    await expect(container).toHaveScreenshot('quizflow-loading-reduced.png', { animations: 'disabled' });
  });
});
