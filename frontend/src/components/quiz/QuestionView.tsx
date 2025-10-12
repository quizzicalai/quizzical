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

      {/* Progress moved to the bottom and simplified */}
      {progress && (
        <div className="mt-8 text-sm font-medium text-muted">
          {/* Only show current, not "of total" */}
          Question {progress.current}
        </div>
      )}
    </div>
  );
}
