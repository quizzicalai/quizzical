// frontend/src/components/quiz/QuestionView.tsx
import React, { useEffect, useRef } from 'react';
import { AnswerGrid } from './AnswerGrid';
import type { Question } from '../../types/quiz';

type QuestionViewProps = {
  question: Question | null;
  onSelectAnswer: (answerId: string) => void;
  isLoading: boolean;
  inlineError: string | null;
  onRetry: () => void;
  progress: {
    current: number;
    total: number;
  };
  selectedAnswerId?: string | null;
};

export function QuestionView({
  question,
  onSelectAnswer,
  isLoading,
  inlineError,
  onRetry,
  progress,
  selectedAnswerId,
}: QuestionViewProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const completionPercent = Math.max(0, Math.min(100, Math.round((progress.current / Math.max(1, progress.total)) * 100)));

  useEffect(() => {
    if (question?.id) {
      headingRef.current?.focus();
    }
  }, [question?.id]);

  if (!question) {
    return null;
  }

  return (
    <div className="max-w-3xl mx-auto text-center">
      {progress && (
        <div className="mb-5">
          <div className="mb-2 flex items-center justify-between gap-3 text-xs font-semibold uppercase tracking-wide text-muted">
            <span>Question {progress.current} of {progress.total}</span>
            <span className="rounded-full border border-border/70 bg-card px-2.5 py-1 text-[10px] text-muted">
              {completionPercent}% complete
            </span>
          </div>
          <div
            role="progressbar"
            aria-label="Question progress"
            aria-valuemin={1}
            aria-valuemax={Math.max(1, progress.total)}
            aria-valuenow={Math.min(progress.total, Math.max(1, progress.current))}
            className="mx-auto h-2 w-full max-w-xs overflow-hidden rounded-full bg-border/70"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-200"
              style={{ width: `${completionPercent}%` }}
            />
          </div>
        </div>
      )}

      {/* Title: same font as landing page title, but smaller */}
      <h2
        ref={headingRef}
        tabIndex={-1}
        aria-live="polite"
        className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-fg mb-6 outline-none"
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

      <div className="mt-8 text-xs font-medium uppercase tracking-wide text-muted/90">
        Keep going, you are almost there.
      </div>
    </div>
  );
}
