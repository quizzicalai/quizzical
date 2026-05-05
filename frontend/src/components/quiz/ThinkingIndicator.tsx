// frontend/src/components/quiz/ThinkingIndicator.tsx
//
// Small "AI thinking" widget shown next to the per-question status text.
//
// Both states render the SAME two dots in the SAME positions:
//   - Larger, darker dot in `bg-primary` at the bottom-left.
//   - Smaller, lighter dot in `bg-primary/50` at the top-right (slightly
//     up and to the right, slightly smaller). Same primary blue family
//     as the global WhimsySprite loader shown during synopsis / baseline
//     question generation.
//
// State difference (AC-PROD-R13-DOTS-1/2):
//   - thinking=false → both dots are still. Reads as a quiet
//     punctuation mark next to the status row.
//   - thinking=true  → the container rotates so the dots orbit each
//     other. Visually it's the same two dots that "just started
//     spinning" — exactly as the global synopsis spinner reads.
//
// The status text rendered alongside this indicator is owned by the
// parent so it can choose between LLM-generated `progress_phrase` and
// a local fallback. This component renders only the icon so it
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

// Both states share the same bounding box so the row never reflows when
// the indicator toggles between idle and thinking.
const BOX_CLASS: Record<'sm' | 'md', string> = {
  sm: 'w-6 h-5',
  md: 'w-8 h-7',
};

// AC-PROD-R13-DOTS-1 — two-dot palette + sizing. Dark dot is the primary
// blue; light dot is the same hue at 50% opacity (matches the global
// WhimsySprite spinner palette). Light dot is one Tailwind step smaller.
const DARK_DOT_CLASS: Record<'sm' | 'md', string> = {
  sm: 'w-2 h-2',
  md: 'w-2.5 h-2.5',
};
const LIGHT_DOT_CLASS: Record<'sm' | 'md', string> = {
  sm: 'w-1.5 h-1.5',
  md: 'w-2 h-2',
};

function Dots({ size }: { size: 'sm' | 'md' }) {
  return (
    <>
      {/* Dark dot, bottom-left. */}
      <span
        aria-hidden="true"
        data-testid="thinking-indicator-dot-dark"
        className={clsx(
          'absolute bottom-0 left-0 inline-block rounded-full bg-primary',
          DARK_DOT_CLASS[size],
        )}
      />
      {/* Light dot, top-right (up and to the right, slightly smaller). */}
      <span
        aria-hidden="true"
        data-testid="thinking-indicator-dot-light"
        className={clsx(
          'absolute top-0 right-0 inline-block rounded-full bg-primary/50',
          LIGHT_DOT_CLASS[size],
        )}
      />
    </>
  );
}

export function ThinkingIndicator({
  thinking,
  size = 'sm',
  className,
  ariaLabel = 'Thinking',
}: ThinkingIndicatorProps) {
  if (thinking) {
    // AC-PROD-R13-DOTS-2 — rotate the container so the two dots orbit
    // each other. Same dots, same colors, same starting positions as
    // the idle state — they simply "started spinning".
    return (
      <span
        role="status"
        aria-label={ariaLabel}
        data-testid="thinking-indicator-spinner"
        className={clsx(
          'relative inline-block animate-spin',
          BOX_CLASS[size],
          className,
        )}
      >
        <Dots size={size} />
      </span>
    );
  }
  // Idle: same two dots, no rotation.
  return (
    <span
      aria-hidden="true"
      data-testid="thinking-indicator-idle"
      className={clsx('relative inline-block', BOX_CLASS[size], className)}
    >
      <Dots size={size} />
    </span>
  );
}
