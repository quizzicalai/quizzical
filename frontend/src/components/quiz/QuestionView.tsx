// src/components/quiz/QuestionView.tsx
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
  selectedAnswerId
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
    <div className="max-w-2xl mx-auto">
      {progress && (
        <div className="text-center mb-4 text-sm font-medium text-muted">
          Question {progress.current} of {progress.total}
        </div>
      )}
      <h2
        ref={headingRef}
        tabIndex={-1}
        aria-live="polite"
        className="text-2xl sm:text-3xl font-bold text-fg text-center mb-6 outline-none"
      >
        {question.text}
      </h2>

      <AnswerGrid
        answers={question.answers}
        onSelect={onSelectAnswer}
        disabled={isLoading}
        selectedId={selectedAnswerId}
      />

      {inlineError && (
        <div className="mt-6 text-center" role="alert">
          <p className="text-red-600 mb-3">{inlineError}</p>
          {onRetry && (
            <button
              type="button"
              className="px-4 py-2 bg-primary text-white rounded hover:opacity-90"
              onClick={onRetry}
            >
              Try Again
            </button>
          )}
        </div>
      )}
    </div>
  );
}