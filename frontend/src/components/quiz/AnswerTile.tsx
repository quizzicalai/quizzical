// src/components/quiz/AnswerTile.tsx
import React, { memo, useState, useEffect } from 'react';
import clsx from 'clsx';
import { Answer } from '../../types/quiz';
import { Logo } from '../../assets/icons/Logo';

// Define the contract for the component's props
type AnswerTileProps = {
  answer: Answer;
  disabled?: boolean;
  isSelected?: boolean;
  onClick: (id: string) => void;
};

/**
 * A memoized, interactive tile representing a single answer option.
 * It displays an image if available, otherwise it falls back to a logo.
 */
export const AnswerTile = memo(function AnswerTile({
  answer,
  disabled = false,
  isSelected = false,
  onClick,
}: AnswerTileProps) {
  const [imageError, setImageError] = useState(false);

  // Reset the image error state if the answer (and its image URL) changes.
  useEffect(() => {
    setImageError(false);
  }, [answer.imageUrl]);

  const handleClick = () => {
    if (!disabled) {
      onClick(answer.id);
    }
  };

  const handleImageError = () => {
    setImageError(true);
  };

  // Determine if we should show the image or the fallback logo
  const showImage = answer.imageUrl && !imageError;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      className={clsx(
        'group text-center rounded-lg border bg-bg p-4 transition-all duration-200',
        'focus:outline-none focus:ring-2 focus:ring-primary/50 focus:ring-offset-2 focus:ring-offset-bg',
        {
          'opacity-60 cursor-not-allowed': disabled && !isSelected,
          'border-primary ring-2 ring-primary/50 shadow-lg': isSelected,
          'hover:border-primary hover:shadow-md': !disabled && !isSelected,
          'opacity-100 cursor-wait': isSelected && disabled,
        }
      )}
      aria-pressed={isSelected}
      aria-label={`Select answer: ${answer.text}`}
    >
      <div className="mb-3 h-32 w-full rounded-md overflow-hidden flex items-center justify-center">
        {showImage ? (
          <img
            src={answer.imageUrl}
            alt={answer.imageAlt || `Image for: ${answer.text}`}
            className="h-full w-full object-cover transition-transform group-hover:scale-105"
            onError={handleImageError}
            loading="lazy"
          />
        ) : (
          // Fallback to the Logo component
          <Logo className="w-16 h-16 text-muted group-hover:text-primary transition-colors" />
        )}
      </div>
      
      <span className="font-medium text-fg leading-tight">
        {answer.text}
      </span>
    </button>
  );
});
