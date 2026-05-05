// frontend/src/components/quiz/QuestionView.thinking.ct.spec.tsx
//
// AC-PROD-R13 component-test coverage for the upper-right two-dot
// "AI thinking" indicator. Mounts QuestionView DIRECTLY (no parent
// store/router) so the assertions reflect what the component
// guarantees regardless of how QuizFlowPage wires it.
//
// What this catches:
//   - VIS-1: the row ALWAYS renders, including idle + no progress
//     phrase (the exact production regression).
//   - DOTS-1: dark dot is bg-primary bottom-left; light dot is
//     bg-primary/50 top-right and smaller (verified via boundingBox
//     in a real browser, so a future Tailwind purge of either class
//     would be detected).
//   - DOTS-2: setting isLoading swaps the idle wrapper for an
//     animate-spin wrapper while keeping the same two dots mounted.
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { QuestionView } from './QuestionView';
import type { Question } from '../../types/quiz';

const QUESTION_FIXTURE: Question = {
  id: 'q-ct-1',
  text: 'Which sounds most like you on a Saturday morning?',
  questionNumber: 2,
  answers: [
    { id: 'a1', text: 'Big breakfast with friends' },
    { id: 'a2', text: 'Long quiet walk' },
    { id: 'a3', text: 'Catch up on a project' },
    { id: 'a4', text: 'Sleep in until noon' },
  ],
} as Question;

test.describe('QuestionView — two-dot ThinkingIndicator (CT)', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });

  test('AC-PROD-R13-VIS-1 + DOTS-1: idle question shows two static dots (regression: row was hidden)', async ({ mount }) => {
    const component = await mount(
      <QuestionView
        question={QUESTION_FIXTURE}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );

    const row = component.getByTestId('quiz-thinking-row');
    await expect(row).toBeVisible();

    const idle = component.getByTestId('thinking-indicator-idle');
    await expect(idle).toBeVisible();
    await expect(component.getByTestId('thinking-indicator-spinner')).toHaveCount(0);

    const dark = component.getByTestId('thinking-indicator-dot-dark');
    const light = component.getByTestId('thinking-indicator-dot-light');
    await expect(dark).toBeVisible();
    await expect(light).toBeVisible();

    // Palette: dark=bg-primary (no opacity slash), light=bg-primary/50.
    await expect(dark).toHaveClass(/(^|\s)bg-primary(\s|$)/);
    await expect(light).toHaveClass(/bg-primary\/50/);

    // Layout: light dot positioned top-right, dark dot bottom-left.
    await expect(light).toHaveClass(/top-0/);
    await expect(light).toHaveClass(/right-0/);
    await expect(dark).toHaveClass(/bottom-0/);
    await expect(dark).toHaveClass(/left-0/);

    // Geometric verification — light dot is up-and-right of dark and
    // smaller. This is what a user actually sees, independent of the
    // class names.
    const darkBox = await dark.boundingBox();
    const lightBox = await light.boundingBox();
    expect(darkBox && lightBox).toBeTruthy();
    if (darkBox && lightBox) {
      expect(lightBox.x).toBeGreaterThan(darkBox.x);   // right of dark
      expect(lightBox.y).toBeLessThan(darkBox.y);      // above dark
      expect(lightBox.width).toBeLessThan(darkBox.width); // smaller
    }

    // Idle state has no role=status (only the spinner exposes it).
    await expect(component.locator('[role="status"]')).toHaveCount(0);
  });

  test('AC-PROD-R13-DOTS-2: same two dots get an animate-spin wrapper while loading', async ({ mount }) => {
    const component = await mount(
      <QuestionView
        question={QUESTION_FIXTURE}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
      />
    );

    const spinner = component.getByTestId('thinking-indicator-spinner');
    await expect(spinner).toBeVisible();
    await expect(spinner).toHaveClass(/animate-spin/);
    await expect(spinner).toHaveAttribute('role', 'status');

    // Same two dots, same palette, just inside a spinning container.
    await expect(component.getByTestId('thinking-indicator-dot-dark')).toBeVisible();
    await expect(component.getByTestId('thinking-indicator-dot-light')).toBeVisible();
    await expect(component.getByTestId('thinking-indicator-idle')).toHaveCount(0);

    // While loading, a phrase (rotating placeholder) is always shown so
    // the user knows the agent is thinking.
    const phrase = component.getByTestId('quiz-progress-phrase');
    await expect(phrase).toBeVisible();
    const initial = (await phrase.textContent())?.trim() ?? '';
    expect(initial.length).toBeGreaterThan(0);
  });
});
