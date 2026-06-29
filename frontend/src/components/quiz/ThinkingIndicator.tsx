// frontend/src/components/quiz/ThinkingIndicator.tsx
//
// Small "AI thinking" widget shown next to the per-question status text.
//
// UX REDESIGN (2026-06-29, owner-approved): the old two indigo `bg-primary`
// dots read as unfinished. This now renders a single, smooth circular
// SPINNER in the LIKED sea-blue `compliment` token (#0079AE) so the active
// state confidently signals "the AI is working". The idle state renders the
// SAME-SIZED static ring at a quiet, low opacity so it reads as a calm,
// finished presence marker. Both states share the identical bounding box,
// so the status row never reflows when toggling idle <-> thinking.
//
//   - thinking=false → static, faint ring. Quiet "finished" presence.
//   - thinking=true  → the ring's arc spins (animate-spin). This spinner is
//                      INTENTIONALLY exempt from prefers-reduced-motion (via an
//                      index.css exemption keyed on its data-testid), so it
//                      always conveys progress — owner-requested.
//
// The status text rendered alongside this indicator is owned by the parent
// (QuestionView) so it can choose between the LLM-generated `progress_phrase`
// and a local fallback. This component renders only the icon so it composes
// cleanly inside any flex row.
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

// Both states share the same square bounding box so the row never reflows
// when the indicator toggles between idle and thinking.
const BOX_CLASS: Record<'sm' | 'md', string> = {
  sm: 'w-4 h-4',
  md: 'w-5 h-5',
};

// A small circular spinner drawn with SVG so the rotating arc is smooth at
// any size. The full ring is faint; a single bright quarter-arc rides on top
// in the sea-blue `compliment` token. When idle we drop the bright arc and
// dim the ring so it reads as a quiet, settled dot.
function Ring({ spinning }: { spinning: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className="h-full w-full"
      aria-hidden="true"
    >
      {/* Faint full track. */}
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="3"
        className={spinning ? 'opacity-25' : 'opacity-30'}
      />
      {/* Bright leading arc — only while actively thinking. */}
      {spinning && (
        <path
          d="M21 12a9 9 0 0 0-9-9"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}

export function ThinkingIndicator({
  thinking,
  size = 'sm',
  className,
  ariaLabel = 'Thinking',
}: ThinkingIndicatorProps) {
  if (thinking) {
    // Active: smooth spinner in the sea-blue `compliment` accent. This spinner
    // spins UNCONDITIONALLY — it is exempt from prefers-reduced-motion (see the
    // index.css exemption keyed on this data-testid) so progress is always shown.
    return (
      <span
        role="status"
        aria-label={ariaLabel}
        data-testid="thinking-indicator-spinner"
        className={clsx(
          'relative inline-flex items-center justify-center text-compliment animate-spin',
          BOX_CLASS[size],
          className,
        )}
      >
        <Ring spinning />
      </span>
    );
  }
  // Idle: same-sized static ring, dimmed. No rotation, no role=status.
  return (
    <span
      aria-hidden="true"
      data-testid="thinking-indicator-idle"
      className={clsx(
        'relative inline-flex items-center justify-center text-compliment',
        BOX_CLASS[size],
        className,
      )}
    >
      <Ring spinning={false} />
    </span>
  );
}
