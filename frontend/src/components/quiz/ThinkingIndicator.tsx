// frontend/src/components/quiz/ThinkingIndicator.tsx
//
// Small "AI thinking" widget shown next to the per-question status text.
// - When `thinking` is true → three-dot bouncing spinner in `bg-primary`
//   (AC-PROD-R7-TW-DOTS-1) — same colour as the global quiz spinner.
// - When `thinking` is false → a still "therefore" glyph (∴) rendered in
//   `text-primary` and sized to share the spinner's bounding box
//   (AC-PROD-R7-TW-GLYPH-1).
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

// AC-PROD-R7-TW-DOTS-1 / AC-PROD-R7-TW-GLYPH-1 — both states must share
// the same bounding box so the row never reflows when the spinner toggles
// to the idle glyph.
const BOX_CLASS: Record<'sm' | 'md', string> = {
  sm: 'w-7 h-5',
  md: 'w-8 h-6',
};
// AC-PROD-R8-GLYPH-1 — idle ∴ glyph slightly larger than the spinner row
// so it reads as a deliberate punctuation mark rather than a stray dot,
// and tilted ~12° to feel hand-drawn.
const GLYPH_TEXT_CLASS: Record<'sm' | 'md', string> = {
  sm: 'text-xl leading-none',
  md: 'text-2xl leading-none',
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
        role="status"
        aria-label={ariaLabel}
        data-testid="thinking-indicator-spinner"
        className={clsx(
          'inline-flex items-center justify-center',
          BOX_CLASS[size],
          className,
        )}
      >
        {/* AC-PROD-R8-SPINNER-1 — same circular spinner primitive used
            globally during quiz generation, sized to share the bounding
            box of the idle ∴ glyph. */}
        <span
          aria-hidden="true"
          className={clsx(
            'animate-spin rounded-full border-2 border-primary border-t-transparent',
            size === 'sm' ? 'w-4 h-4' : 'w-5 h-5',
          )}
        />
      </span>
    );
  }
  // Idle: still "therefore" glyph (Unicode 0x2234) — primary colour, same
  // bounding box as the spinner. Decorative; the accompanying status text
  // is the actual content for screen readers.
  return (
    <span
      aria-hidden="true"
      data-testid="thinking-indicator-idle"
      className={clsx(
        // AC-PROD-R8-GLYPH-1 — same primary blue as the global spinner,
        // slightly tilted so the punctuation feels intentional.
        'inline-flex items-center justify-center text-primary rotate-12 font-semibold',
        BOX_CLASS[size],
        GLYPH_TEXT_CLASS[size],
        className,
      )}
    >
      ∴
    </span>
  );
}
