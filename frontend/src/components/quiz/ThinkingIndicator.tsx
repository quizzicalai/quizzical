// frontend/src/components/quiz/ThinkingIndicator.tsx
//
// Small "AI thinking" widget shown next to the per-question status text.
// - When `thinking` is true → animated spinner (same primitive as the
//   quiz-loading spinner) signaling the agent is generating the next step.
// - When `thinking` is false → a still "therefore" glyph (∴) acting as a
//   subtle marker that the line is the agent's voice.
//
// The status text rendered alongside this indicator is owned by the parent
// (so the parent can choose between LLM-generated `progress_phrase` and a
// local fallback). This component intentionally renders only the icon so it
// composes cleanly inside any flex row.
import React from 'react';
import clsx from 'clsx';

export type ThinkingIndicatorProps = {
  /** True while the agent is generating the next question / final result. */
  thinking: boolean;
  /** Optional sizing override. Defaults to `sm`. */
  size?: 'sm' | 'md';
  className?: string;
  /** Accessible label for the spinner state (default "Thinking"). */
  ariaLabel?: string;
};

const SIZE_CLASS: Record<'sm' | 'md', string> = {
  sm: 'w-4 h-4 border-2',
  md: 'w-5 h-5 border-2',
};

export function ThinkingIndicator({
  thinking,
  size = 'sm',
  className,
  ariaLabel = 'Thinking',
}: ThinkingIndicatorProps) {
  if (thinking) {
    return (
      <span
        className={clsx('inline-flex items-center justify-center', className)}
        data-testid="thinking-indicator-spinner"
      >
        <span
          role="status"
          aria-label={ariaLabel}
          className={clsx(
            'animate-spin rounded-full border-primary border-t-transparent',
            SIZE_CLASS[size],
          )}
        />
      </span>
    );
  }
  // Idle: still "therefore" glyph (Unicode 0x2234). Decorative — the
  // accompanying status text is the actual content for screen readers.
  return (
    <span
      aria-hidden="true"
      data-testid="thinking-indicator-idle"
      className={clsx(
        'inline-flex items-center justify-center text-primary/80',
        size === 'sm' ? 'text-base leading-none' : 'text-lg leading-none',
        className,
      )}
    >
      ∴
    </span>
  );
}
