// src/components/quiz/AnswerTile.tsx
import React, { memo, useCallback } from 'react';
import clsx from 'clsx';
import { Answer } from '../../types/quiz'; // Import the shared Answer type

// Define the contract for the component's props
type AnswerTileProps = {
  answer: Answer;
  disabled?: boolean;
  onClick: (id: string) => void;
};

/**
 * A memoized, interactive tile representing a single answer option.
 */
export const AnswerTile = memo(function AnswerTile({ answer, disabled = false, onClick }: AnswerTileProps) {
  
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
      aria-label={`Select answer: ${answer.text}`}
    >
      {answer.imageUrl && (
        <img 
          src={answer.imageUrl} 
          alt={answer.imageAlt || ''} 
          className="mb-3 h-32 w-full rounded-md object-cover" 
          loading="lazy"
        />
      )}
      <span className="text-center font-medium text-fg leading-tight">
        {answer.text}
      </span>
    </button>
  );
});