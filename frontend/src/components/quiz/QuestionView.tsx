// frontend/src/components/quiz/QuestionView.tsx
import React, { useEffect, useRef } from 'react';
import { AnswerGrid } from './AnswerGrid';
import { ThinkingIndicator } from './ThinkingIndicator';
import type { Question } from '../../types/quiz';

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

  if (!question) {
    return null;
  }

  // Resolve progress fields, preferring explicit props over the question payload.
  const phrase = (progressPhrase ?? question.progressPhrase ?? '').trim();
  const number =
    typeof questionNumber === 'number' && questionNumber > 0
      ? Math.floor(questionNumber)
      : typeof question.questionNumber === 'number' && question.questionNumber > 0
        ? Math.floor(question.questionNumber)
        : null;

  // The thinking row always renders (so its absence doesn't cause CLS when
  // a phrase arrives async). When we don't have a phrase yet we fall back
  // to a generic "Thinking…" placeholder while loading, or leave it blank
  // when idle so the UI stays quiet.
  const showThinkingRow = isLoading || !!phrase;
  const displayPhrase = phrase || (isLoading ? 'Thinking…' : '');

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
