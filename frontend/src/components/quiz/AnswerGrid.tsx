// src/components/quiz/AnswerGrid.tsx
import React, { memo, useCallback } from 'react';
import clsx from 'clsx';
import { Answer } from '../../types/quiz'; // Import the shared type

type AnswerTileProps = {
  answer: Answer;
  disabled: boolean;
  onClick: (id: string) => void;
};

const AnswerTile = memo(function AnswerTile({ answer, disabled, onClick }: AnswerTileProps) {
  const handleClick = useCallback(() => {
    if (!disabled) {
      onClick(answer.id);
    }
  }, [disabled, onClick, answer.id]);

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      className={clsx(
        'group text-left rounded-lg border bg-bg p-4 transition-all',
        'hover:border-primary hover:shadow-md focus:outline-none focus:ring-2 focus:ring-primary/50',
        disabled && 'opacity-60 cursor-not-allowed'
      )}
    >
      {answer.imageUrl && (
        <img
          src={answer.imageUrl}
          alt={answer.imageAlt || ''}
          loading="lazy"
          className="mb-3 h-32 w-full rounded-md object-cover"
        />
      )}
      <p className="text-base text-fg">{answer.text}</p>
    </button>
  );
});

type AnswerGridProps = {
  answers: Answer[];
  disabled?: boolean;
  onSelect: (answerId: string) => void;
};

export function AnswerGrid({ answers, disabled = false, onSelect }: AnswerGridProps) {
  if (!Array.isArray(answers) || answers.length === 0) {
    return null;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {answers.map((answer) => (
        <AnswerTile
          key={answer.id}
          answer={answer}
          disabled={disabled}
          onClick={onSelect}
        />
      ))}
    </div>
  );
}