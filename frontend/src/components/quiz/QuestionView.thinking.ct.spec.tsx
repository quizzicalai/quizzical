// frontend/src/components/quiz/QuestionView.thinking.ct.spec.tsx
//
// Component-test coverage for the upper-right agent-status indicator.
// Mounts QuestionView DIRECTLY (no parent store/router) so the assertions
// reflect what the component guarantees regardless of how QuizFlowPage
// wires it.
//
// UX REDESIGN (2026-06-29, owner-approved): the indicator is now a single
// smooth spinner in the sea-blue `compliment` accent (active) / a quiet
// static ring (idle), replacing the two indigo dots.
//
// What this catches:
//   - VIS-1: the row ALWAYS renders, including idle + no progress phrase
//     (the exact production regression).
//   - Idle shows the quiet static ring with no role=status.
//   - Setting isLoading swaps the idle ring for an animate-spin spinner
//     that exposes role=status, and a phrase is always shown.
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

test.describe('QuestionView — agent-status indicator (CT)', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });

  test('VIS-1: idle question shows the quiet static ring (regression: row was hidden)', async ({ mount }) => {
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

    // Sea-blue compliment accent, not the old indigo dots.
    await expect(idle).toHaveClass(/text-compliment/);

    // Idle state has no role=status (only the active spinner exposes it).
    await expect(component.locator('[role="status"]')).toHaveCount(0);
  });

  test('active: idle ring is swapped for an animate-spin compliment spinner while loading', async ({ mount }) => {
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
    await expect(spinner).toHaveClass(/text-compliment/);
    await expect(spinner).toHaveAttribute('role', 'status');
    await expect(component.getByTestId('thinking-indicator-idle')).toHaveCount(0);

    // While loading, a phrase (rotating placeholder) is always shown so
    // the user knows the agent is thinking.
    const phrase = component.getByTestId('quiz-progress-phrase');
    await expect(phrase).toBeVisible();
    const initial = (await phrase.textContent())?.trim() ?? '';
    expect(initial.length).toBeGreaterThan(0);
  });
});
