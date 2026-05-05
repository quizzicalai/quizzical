// frontend/tests/ct/QuestionView.thinking.ct.spec.tsx
//
// AC-PROD-R13 component-test coverage for the upper-right two-dot
// "AI thinking" indicator on the QuizFlowPage question view.
//
// These tests render the REAL QuizFlowPage with the CT mock store
// flipped to `currentView: 'question'`, then verify:
//   - VIS-1: idle state ALWAYS shows the two-dot row (the regression
//     in production: the row was hidden when no progressPhrase was
//     present, so users on a settled question saw nothing).
//   - DOTS-1: idle row contains the dark+light dots in the documented
//     positions and bg-primary palette.
//   - DOTS-2: flipping `isPolling` (loading) wraps the same two dots
//     in an `animate-spin` container without unmounting them.
//   - ROTATE-1: while loading and no upstream phrase, the placeholder
//     phrase changes after ~3s.
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QuizFlowPage } from '../../pages/QuizFlowPage';
import '../../../tests/ct/fixtures/config';

const QUESTION_FIXTURE = {
  id: 'q-ct-1',
  text: 'Which sounds most like you on a Saturday morning?',
  questionNumber: 2,
  answers: [
    { id: 'a1', text: 'Big breakfast with friends' },
    { id: 'a2', text: 'Long quiet walk' },
    { id: 'a3', text: 'Catch up on a project' },
    { id: 'a4', text: 'Sleep in until noon' },
  ],
};

test.describe('<QuizFlowPage /> question view — two-dot ThinkingIndicator (CT)', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => {
      window.__ct_quiz_reset?.();
    });
  });

  test('AC-PROD-R13-VIS-1 + DOTS-1: idle question shows two static dots (regression: row was hidden)', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );
    await page.evaluate((q) => {
      window.__ct_quiz_set?.({
        quizId: 'ct-quiz-1',
        currentView: 'question',
        viewData: q,
        isPolling: false,
        isSubmittingAnswer: false,
      });
    }, QUESTION_FIXTURE);

    const row = page.getByTestId('quiz-thinking-row');
    await expect(row).toBeVisible();

    const idle = page.getByTestId('thinking-indicator-idle');
    await expect(idle).toBeVisible();
    await expect(page.getByTestId('thinking-indicator-spinner')).toHaveCount(0);

    const dark = page.getByTestId('thinking-indicator-dot-dark');
    const light = page.getByTestId('thinking-indicator-dot-light');
    await expect(dark).toBeVisible();
    await expect(light).toBeVisible();

    // Palette: dark=bg-primary (no opacity slash), light=bg-primary/50.
    await expect(dark).toHaveClass(/(^|\s)bg-primary(\s|$)/);
    await expect(light).toHaveClass(/bg-primary\/50/);

    // Layout: light dot is up-and-right, dark dot bottom-left.
    await expect(light).toHaveClass(/top-0/);
    await expect(light).toHaveClass(/right-0/);
    await expect(dark).toHaveClass(/bottom-0/);
    await expect(dark).toHaveClass(/left-0/);

    // Light dot is geometrically up-and-to-the-right of the dark dot.
    const darkBox = await dark.boundingBox();
    const lightBox = await light.boundingBox();
    expect(darkBox && lightBox).toBeTruthy();
    if (darkBox && lightBox) {
      expect(lightBox.x).toBeGreaterThan(darkBox.x); // right of dark
      expect(lightBox.y).toBeLessThan(darkBox.y);    // above dark
      expect(lightBox.width).toBeLessThan(darkBox.width); // smaller
    }
  });

  test('AC-PROD-R13-DOTS-2: same two dots get an animate-spin wrapper while loading', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );
    // Start idle on a question, then flip to loading (isPolling=true)
    // simulating the gap between user submit and next-question arrival.
    await page.evaluate((q) => {
      window.__ct_quiz_set?.({
        quizId: 'ct-quiz-1',
        currentView: 'question',
        viewData: q,
        isPolling: false,
      });
    }, QUESTION_FIXTURE);
    await expect(page.getByTestId('thinking-indicator-idle')).toBeVisible();

    await page.evaluate(() => {
      window.__ct_quiz_set?.({ isPolling: true });
    });

    const spinner = page.getByTestId('thinking-indicator-spinner');
    await expect(spinner).toBeVisible();
    await expect(spinner).toHaveClass(/animate-spin/);
    await expect(spinner).toHaveAttribute('role', 'status');

    // Same two dots, same palette, just inside a spinning container.
    await expect(page.getByTestId('thinking-indicator-dot-dark')).toBeVisible();
    await expect(page.getByTestId('thinking-indicator-dot-light')).toBeVisible();
    await expect(page.getByTestId('thinking-indicator-idle')).toHaveCount(0);
  });

  test('AC-PROD-R13-ROTATE-1: placeholder phrase rotates while loading without upstream phrase', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );
    await page.evaluate((q) => {
      window.__ct_quiz_set?.({
        quizId: 'ct-quiz-1',
        currentView: 'question',
        viewData: { ...q, progressPhrase: undefined },
        isPolling: true,
      });
    }, QUESTION_FIXTURE);

    const phrase = page.getByTestId('quiz-progress-phrase');
    await expect(phrase).toBeVisible();
    const first = (await phrase.textContent())?.trim() ?? '';
    expect(first.length).toBeGreaterThan(0);

    // Rotation interval is 3000ms — wait a hair past two ticks and
    // require at least one change. (Pool has 50+ phrases so collision
    // probability across two distinct rotations is negligible.)
    await page.waitForTimeout(6500);
    const later = (await phrase.textContent())?.trim() ?? '';
    expect(later).not.toBe(first);
  });
});
