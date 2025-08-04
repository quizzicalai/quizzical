// src/components/quiz/AnswerGrid.jsx
import React, { memo } from 'react';
import clsx from 'clsx';

const AnswerTile = memo(function AnswerTile({ answer, disabled, onClick }) {
  const handleClick = () => {
    if (!disabled) {
      onClick(answer.id);
    }
  };

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

export function AnswerGrid({ answers, disabled = false, onSelect }) {
  if (!Array.isArray(answers) || answers.length === 0) {
    return null;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {answers.map((answer) => (
        <AnswerTile
          key={answer.id} // Use stable ID from backend
          answer={answer}
          disabled={disabled}
          onClick={onSelect}
        />
      ))}
    </div>
  );
}