// src/components/quiz/AnswerGrid.tsx
import React, { memo, useCallback } from 'react';
import clsx from 'clsx';
import type { Answer } from '../../types/quiz';
import { safeImageUrl } from '../../utils/safeImageUrl';

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
      className={clsx(
        // Unified subtle-grey resting border (--color-border / slate-200) via
        // the shared `border-border` token — consistent across the app.
        // Replaces the prior near-black borders (inline muted/0.55 rest +
        // hover:border-fg + border-fg/30 selected).
        'group relative text-left rounded-2xl border border-border bg-card p-4 sm:p-5 select-none',
        'transition-[transform,box-shadow,border-color,background-color] duration-150 ease-out-token',
        'shadow-sm',
        // Cursors (no system spinner)
        !disabled ? 'cursor-pointer' : 'cursor-not-allowed',
        // Hover → subtle grey deepen + lift (never a black outline)
        !disabled && 'hover:-translate-y-0.5 hover:shadow-md hover:border-muted/40',
        // Focus ring
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-card',
        // Active
        !disabled && 'active:translate-y-0 active:shadow-sm active:scale-[0.995]',
        // Selection carried by the primary ring + shadow; border stays grey.
        isSelected && 'ring-2 ring-ring border-border shadow-md'
      )}
    >
      {/* AC-UX-2026-05-25-PART3 item 5 — removed the overlay Spinner
          that previously painted a contrast scrim over the selected
          answer while the agent thought. The selected-state ring +
          aria-busy already conveys "this is your pick", and the
          top-right ThinkingIndicator now carries the agent's busy
          state with a clear blue indicator + label. Duplicating it on
          the tile pulled the user's eye away from the upper-right
          status row. */}

      {(() => {
        const url = safeImageUrl(answer.imageUrl);
        return url && (
          <img
            src={url}
            alt={answer.imageAlt || ''}
            loading="lazy"
            className="mb-3 h-32 w-full rounded-md object-cover transition-transform duration-150 group-hover:scale-[1.02]"
          />
        );
      })()}
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
    // UX-MOTION-2026-06-29 — `animate-answer-grid` gives each tile a subtle
    // staggered slide-up/fade entrance (its direct children are the per-answer
    // wrappers below). Decorative; neutralized under prefers-reduced-motion.
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 animate-answer-grid">
      {answers.map((answer, idx) => {
        const isOddLast = answers.length % 2 === 1 && idx === answers.length - 1;

        return (
          <div
            key={answer.id}
            className={clsx(
              // On wide screens, make the last orphan span both cols and center it.
              // Width = 50% minus half the gap (gap-4 = 1rem → 0.5rem).
              isOddLast && 'sm:col-span-2 sm:justify-self-center sm:w-[calc(50%-0.5rem)]'
            )}
          >
            <AnswerTile
              answer={answer}
              disabled={disabled}
              isSelected={answer.id === selectedId}
              onClick={onSelect}
            />
          </div>
        );
      })}
    </div>
  );
}