// src/components/quiz/AnswerGrid.tsx
import React, { memo, useCallback } from 'react';
import clsx from 'clsx';
import type { Answer } from '../../types/quiz';
import { Spinner } from '../common/Spinner';

type AnswerTileProps = {
  answer: Answer;
  disabled: boolean;
  isSelected: boolean;
  onClick: (id: string) => void;
};

const AnswerTile = memo(function AnswerTile({ answer, disabled, isSelected, onClick }: AnswerTileProps) {
  const handleClick = useCallback(() => { if (!disabled) onClick(answer.id); }, [disabled, onClick, answer.id]);

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      aria-pressed={isSelected}
      aria-busy={isSelected && disabled ? true : undefined}
      aria-label={`Select answer: ${answer.text}`}
      style={{ borderColor: 'rgb(var(--color-muted) / 0.55)' }}
      className={clsx(
        'group relative text-left rounded-2xl border bg-card p-4 sm:p-5 select-none',
        'transition-[transform,box-shadow,border-color,background-color] duration-150',
        'shadow-sm',
        // Cursors (no system spinner)
        !disabled ? 'cursor-pointer' : 'cursor-not-allowed',
        // Hover → dark outline + lift
        !disabled && 'hover:-translate-y-0.5 hover:shadow-md hover:border-fg',
        // Focus ring
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-card',
        // Active
        !disabled && 'active:translate-y-0 active:shadow-sm active:scale-[0.995]',
        disabled && !isSelected && 'opacity-60',
        isSelected && 'ring-2 ring-ring border-fg/30 shadow-md'
      )}
    >
      {isSelected && disabled && (
        <div className="absolute inset-0 bg-white/50 dark:bg-black/40 flex items-center justify-center rounded-2xl text-fg cursor-default">
          <Spinner size="md" />
        </div>
      )}

      {answer.imageUrl && (
        <img
          src={answer.imageUrl}
          alt={answer.imageAlt || ''}
          loading="lazy"
          className="mb-3 h-32 w-full rounded-md object-cover transition-transform duration-150 group-hover:scale-[1.02]"
        />
      )}
      <p className="text-base text-fg font-medium leading-tight">{answer.text}</p>
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
  if (!Array.isArray(answers) || answers.length === 0) return null;

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
