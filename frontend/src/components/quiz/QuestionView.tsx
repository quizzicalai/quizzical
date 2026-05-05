// frontend/src/components/quiz/QuestionView.tsx
import React, { useEffect, useRef, useState } from 'react';
import { AnswerGrid } from './AnswerGrid';
import { ThinkingIndicator } from './ThinkingIndicator';
import type { Question } from '../../types/quiz';

// AC-PROD-R6-FE-ROTATE-1 — curated client-side phrase pool the FE cycles
// through while waiting for the agent's next step. Same narrative voice as
// the BE `ALL_NARROWING_PHRASES` pool (see
// `backend/app/agent/progress_phrases.py`) but kept short on purpose so it
// fits the upper-right pill at every breakpoint.
// eslint-disable-next-line react-refresh/only-export-components
export const THINKING_PHRASES: readonly string[] = [
  'Thinking…',
  'Weighing your answer…',
  'Looking for patterns…',
  'Cross-checking clues…',
  'Narrowing the field…',
  'Sketching a hypothesis…',
  'Picking the next angle…',
  'Comparing your choices…',
  'Listening between the lines…',
  'Refining the read…',
  'Lining up candidates…',
  'Trying a fresh angle…',
];

const ROTATE_INTERVAL_MS = 2500;

type QuestionViewProps = {
  question: Question | null;
  onSelectAnswer: (answerId: string) => void;
  isLoading: boolean;
  inlineError: string | null;
  onRetry: () => void;
  /**
   * 1-based ordinal of the current question. The agent ends the quiz on
   * either max-questions OR a confidence threshold, so we deliberately do
   * not show a denominator like "of 20" — that would mislead. Falls back
   * to question.questionNumber when omitted.
   */
  questionNumber?: number;
  /**
   * Short status string ("I'm narrowing in…") shown in the upper-right
   * thinking row alongside the spinner / ∴ glyph. Falls back to
   * question.progressPhrase when omitted.
   */
  progressPhrase?: string;
  selectedAnswerId?: string | null;
};

export function QuestionView({
  question,
  onSelectAnswer,
  isLoading,
  inlineError,
  onRetry,
  questionNumber,
  progressPhrase,
  selectedAnswerId,
}: QuestionViewProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (question?.id) {
      headingRef.current?.focus();
    }
  }, [question?.id]);

  // Resolve progress fields up-front so the rotation hooks below can be
  // declared unconditionally (lint: react-hooks/rules-of-hooks). All values
  // are safe to compute even when `question` is null.
  const phrase = (progressPhrase ?? question?.progressPhrase ?? '').trim();

  // AC-PROD-R6-FE-ROTATE-1/2 — cycle the curated phrase pool every
  // ROTATE_INTERVAL_MS while loading and no upstream phrase is available.
  // The interval is cleared whenever loading stops, an LLM phrase arrives,
  // or the component unmounts.
  const [rotatedIndex, setRotatedIndex] = useState(0);
  const useRotation = isLoading && !phrase;
  useEffect(() => {
    if (!useRotation) {
      setRotatedIndex(0);
      return;
    }
    const id = window.setInterval(() => {
      setRotatedIndex((i) => (i + 1) % THINKING_PHRASES.length);
    }, ROTATE_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [useRotation]);

  if (!question) {
    return null;
  }

  const number =
    typeof questionNumber === 'number' && questionNumber > 0
      ? Math.floor(questionNumber)
      : typeof question.questionNumber === 'number' && question.questionNumber > 0
        ? Math.floor(question.questionNumber)
        : null;

  // The thinking row always renders (so its absence doesn't cause CLS when
  // a phrase arrives async). When we don't have a phrase yet we fall back
  // to a rotating placeholder while loading (AC-PROD-R6-FE-ROTATE-1), or
  // leave it blank when idle so the UI stays quiet.
  const showThinkingRow = isLoading || !!phrase;

  const displayPhrase = phrase || (isLoading ? THINKING_PHRASES[rotatedIndex] : '');

  return (
    <div className="max-w-3xl mx-auto text-center">
      {/* Top status row: AI thinking widget + italic phrase, top-right.
          Spinner while the agent is loading the next step; ∴ when idle. */}
      {showThinkingRow && (
        <div className="mb-5 flex items-center justify-end gap-2 min-h-[1.25rem]">
          <ThinkingIndicator
            thinking={isLoading}
            ariaLabel={displayPhrase || 'Thinking'}
          />
          <span
            className="text-xs sm:text-sm italic text-muted"
            data-testid="quiz-progress-phrase"
            aria-live="polite"
          >
            {displayPhrase}
          </span>
        </div>
      )}

      {/* Question text — sized down per UX feedback (was text-2xl/3xl). */}
      <h2
        ref={headingRef}
        tabIndex={-1}
        aria-live="polite"
        className="font-display text-xl sm:text-2xl font-semibold tracking-tight text-fg mb-6 outline-none"
      >
        {question.text}
      </h2>

      {/* Answers (kept: 1 col → 2 cols responsive) */}
      <AnswerGrid
        answers={question.answers}
        onSelect={onSelectAnswer}
        disabled={isLoading}
        selectedId={selectedAnswerId}
      />

      {/* Error (if any) */}
      {inlineError && (
        <div className="mt-6" role="alert">
          <p className="text-red-600 mb-3">{inlineError}</p>
          {onRetry && (
            <button
              type="button"
              className="px-4 py-2 rounded-lg bg-fg text-card hover:opacity-90 transition"
              onClick={onRetry}
            >
              Try Again
            </button>
          )}
        </div>
      )}

      {/* Bottom: just the current question ordinal — no denominator. */}
      {number !== null && (
        <div
          className="mt-8 text-xs font-medium uppercase tracking-wide text-muted/90"
          data-testid="quiz-question-ordinal"
        >
          Question {number}
        </div>
      )}
    </div>
  );
}
