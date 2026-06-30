// src/components/quiz/AnswerGrid.tsx
import React from 'react';
import clsx from 'clsx';
import type { Answer } from '../../types/quiz';
// #12 (HITLIST-2026-06-30) — render the canonical exported AnswerTile rather
// than a leaner inline copy. The exported tile adds: a fixed h-32 image box
// (no CLS when a late/null FAL image finally arrives), a skeleton pulse +
// opacity fade-in while the image loads, and a Logo broken-image fallback via
// onError. The previous inline tile was a bare `<img loading="lazy">` with no
// skeleton / onError / fallback, so a late or null image left a blank gap.
import { AnswerTile } from './AnswerTile';

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