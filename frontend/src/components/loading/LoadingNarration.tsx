// frontend/src/components/loading/LoadingNarration.tsx
import React from 'react';

type Line = { atMs: number; text: string };

const DEFAULT_LINES: Line[] = [
  { atMs:     0, text: 'Thinking…' },
  { atMs:  3000, text: 'Researching topic…' },
  { atMs:  6000, text: 'Determining characters…' },
  { atMs:  9000, text: 'Writing character profiles…' },
  { atMs: 12000, text: 'Preparing topic…' },
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
      {/* This is the visible text node – give it a stable test id */}
      <span
        className="text-lg text-[rgb(var(--color-muted))]"
        data-testid="loading-narration-text"
      >
        {text}
      </span>
    </div>
  );
}
