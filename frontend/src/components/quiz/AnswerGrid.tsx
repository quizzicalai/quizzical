// src/components/quiz/AnswerGrid.tsx
import React, { memo, useCallback } from 'react';
import clsx from 'clsx';
import { Answer } from '../../types/quiz';
import { Spinner } from '../common/Spinner';

type AnswerTileProps = {
  answer: Answer;
  disabled: boolean;
  isSelected: boolean;
  onClick: (id: string) => void;
};

const AnswerTile = memo(function AnswerTile({ answer, disabled, isSelected, onClick }: AnswerTileProps) {
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
        'group relative text-left rounded-lg border bg-bg p-4 transition-all',
        'hover:border-primary hover:shadow-md focus:outline-none focus:ring-2 focus:ring-primary/50',
        disabled && 'opacity-60 cursor-not-allowed',
        isSelected && 'border-primary ring-2 ring-primary/50'
      )}
    >
      {isSelected && disabled && (
        <div className="absolute inset-0 bg-white/50 dark:bg-black/50 flex items-center justify-center rounded-lg">
          <Spinner size="md" />
        </div>
      )}
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
  selectedId?: string | null;
};

export function AnswerGrid({ answers, disabled = false, onSelect, selectedId }: AnswerGridProps) {
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
          isSelected={answer.id === selectedId}
          onClick={onSelect}
        />
      ))}
    </div>
  );
}