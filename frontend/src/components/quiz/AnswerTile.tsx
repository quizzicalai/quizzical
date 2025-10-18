// src/components/quiz/AnswerTile.tsx
import React, { memo, useState, useEffect } from 'react';
import clsx from 'clsx';
import type { Answer } from '../../types/quiz';
import { Logo } from '../../assets/icons/Logo';
import { Spinner } from '../common/Spinner';

type AnswerTileProps = {
  answer: Answer;
  disabled?: boolean;
  isSelected?: boolean;
  onClick: (id: string) => void;
};

export const AnswerTile = memo(function AnswerTile({
  answer,
  disabled = false,
  isSelected = false,
  onClick,
}: AnswerTileProps) {
  const [imageError, setImageError] = useState(false);
  useEffect(() => { setImageError(false); }, [answer.imageUrl]);

  const handleClick = () => { if (!disabled) onClick(answer.id); };
  const handleImageError = () => setImageError(true);
  const showImage = !!answer.imageUrl && !imageError;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      aria-pressed={isSelected}
      aria-busy={isSelected && disabled ? true : undefined}
      aria-label={`Select answer: ${answer.text}`}
      // Match landing-pill base outline (muted 55%)
      style={{ borderColor: 'rgb(var(--color-muted) / 0.55)' }}
      className={clsx(
        'w-full',
        'group relative text-left rounded-2xl border bg-card p-4 sm:p-5 select-none',
        'transition-[transform,box-shadow,border-color,background-color] duration-150',
        'shadow-sm',
        !disabled ? 'cursor-pointer' : 'cursor-not-allowed',
        !disabled && 'hover:-translate-y-0.5 hover:shadow-md hover:border-fg',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-card',
        !disabled && 'active:translate-y-0 active:shadow-sm active:scale-[0.995]',
        disabled && !isSelected && 'opacity-60',
        isSelected && 'ring-2 ring-ring border-fg/30 shadow-md'
      )}
    >
      {/* Busy overlay — themed color; does NOT change cursor */}
      {isSelected && disabled && (
        <div className="absolute inset-0 bg-white/50 dark:bg-black/40 flex items-center justify-center rounded-2xl text-fg cursor-default">
          <Spinner size="md" />
        </div>
      )}

      <div className="mb-3 h-32 w-full rounded-md overflow-hidden flex items-center justify-center">
        {showImage ? (
          <img
            src={answer.imageUrl}
            alt={answer.imageAlt || `Image for: ${answer.text}`}
            className="h-full w-full object-cover transition-transform duration-150 group-hover:scale-[1.02]"
            onError={handleImageError}
            loading="lazy"
          />
        ) : (
          <Logo className="w-16 h-16 text-muted group-hover:text-fg transition-colors duration-150" />
        )}
      </div>

      {/* Answer label: same font family as landing subtitle, but smaller */}
      <span className="font-sans font-medium text-fg leading-tight">
        {answer.text}
      </span>
    </button>
  );
});
