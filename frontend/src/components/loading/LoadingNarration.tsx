// frontend/src/components/loading/LoadingNarration.tsx
import React from 'react';

type Line = { atMs: number; text: string };

const DEFAULT_LINES: Line[] = [
  { atMs:     0, text: 'Thinking…' },
  { atMs:  3000, text: 'Researching topic…' },
  { atMs:  6000, text: 'Determining personality types…' },
  { atMs:  9000, text: 'Writing profiles…' },
  { atMs: 12000, text: 'Preparing topic…' },
];

/** NEW: narration specifically for post-synopsis → baseline questions */
// eslint-disable-next-line react-refresh/only-export-components
export const QUIZ_PROGRESS_LINES: Line[] = [
  { atMs:     0, text: 'Thinking…' },
  { atMs:  3000, text: 'Planning quiz…' },
  { atMs:  6000, text: 'Generating questions…' },
  { atMs:  9000, text: 'Refining options…' },
];

/**
 * AC-UX-2026-05-12 — landing-page "preparing" rotation. Replaces the
 * old static "Getting things ready…" copy with a friendly headline plus
 * a rotating sub-message that hints at the breadth of topics Quafel can
 * handle. The first line is the longest-visible default; subsequent lines
 * cycle every few seconds to give the page life while invisible Turnstile
 * resolves.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const LANDING_PREPARING_LINES: Line[] = [
  { atMs:     0, text: "Quafel lets you discover who you are — Myers-Briggs Type, Hogwarts House, Famous Elephant — anything!" },
  { atMs:  4000, text: "You pick the topic, I'll generate the quiz." },
  { atMs:  8000, text: "From philosophers to Pokémon types — if you can name it, I can quiz you on it." },
  { atMs: 12000, text: "Every quiz is generated fresh by an AI agent just for you." },
  { atMs: 16000, text: "Try something silly. Try something deep. Try something you've never thought about." },
];

export type LoadingNarrationProps = {
  lines?: Line[];
  onChangeText?: (t: string) => void;
  tickMs?: number;
  ariaLabel?: string;
};

export function LoadingNarration({
  lines = DEFAULT_LINES,
  onChangeText,
  tickMs = 250,
  ariaLabel = 'Loading',
}: LoadingNarrationProps) {
  const startRef = React.useRef<number>(performance.now());
  const [text, setText] = React.useState<string>(lines[0]?.text ?? 'Loading…');

  React.useEffect(() => {
    let last = '';
    const id = window.setInterval(() => {
      const elapsed = performance.now() - startRef.current;
      const current = lines.reduce((acc, l) => (elapsed >= l.atMs ? l : acc), lines[0]!);
      if (current.text !== last) {
        last = current.text;
        setText(current.text);
        onChangeText?.(current.text);
      }
    }, tickMs);
    return () => window.clearInterval(id);
  }, [lines, onChangeText, tickMs]);

  return (
    <div
      className="flex items-center gap-3"
      role="status"
      aria-live="polite"
      aria-label={ariaLabel}
      data-testid="loading-narration"
    >
      <span className="sr-only">{ariaLabel}</span>
      <span
        className="text-lg text-[rgb(var(--color-muted))]"
        data-testid="loading-narration-text"
      >
        {text}
      </span>
    </div>
  );
}
