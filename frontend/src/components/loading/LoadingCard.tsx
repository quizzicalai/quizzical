// frontend/src/components/loading/LoadingCard.tsx
import React from 'react';
import { HeroCard } from '../layout/HeroCard';
import { WhimsySprite } from './WhimsySprite';
import { LoadingNarration } from './LoadingNarration';
import type { LoadingNarrationProps } from './LoadingNarration';

type LoadingCardProps = {
  lines?: LoadingNarrationProps['lines'];
  /**
   * Optional escape hatch. When provided, a low-emphasis "Start over" link
   * appears after {startOverAfterMs} so a hung / unusually slow generation is
   * never a dead end (owner: no dead ends). Omit to render no escape — this
   * keeps the default (prop-less) usage byte-identical.
   */
  onStartOver?: () => void;
  startOverAfterMs?: number;
  startOverLabel?: string;
};

/**
 * A centered loading strip placed inside the standard hero card.
 * Reuses Phase 0 HeroCard so layout/spacing/hero are identical.
 */
export function LoadingCard({
  lines,
  onStartOver,
  startOverAfterMs = 20000,
  startOverLabel = 'Start over',
}: LoadingCardProps) {
  const [showEscape, setShowEscape] = React.useState(false);

  React.useEffect(() => {
    if (!onStartOver) return;
    const id = window.setTimeout(() => setShowEscape(true), startOverAfterMs);
    return () => window.clearTimeout(id);
  }, [onStartOver, startOverAfterMs]);

  return (
    <HeroCard ariaLabel="Loading card">
      <div className="flex flex-col items-center justify-center gap-4">
        <div className="inline-flex items-center gap-3">
          {/* AC-UX-2026-05-25-PART2 item 3 — the loading card MUST show
              the animated sprite. After the Part 1 idle/spinning split
              the default became idle, which silently regressed every
              "agent is thinking" loading screen to a stationary sprite.
              Explicitly request the spinning state here. */}
          <WhimsySprite spinning />
          <LoadingNarration lines={lines} />
        </div>
        {showEscape && onStartOver && (
          <button
            type="button"
            onClick={onStartOver}
            data-testid="loading-start-over"
            className="text-sm font-medium text-[rgb(var(--color-text-secondary,71_85_105))] underline-offset-4 hover:underline hover:text-fg transition-colors duration-150 ease-out-token focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded"
          >
            This is taking longer than usual — {startOverLabel}
          </button>
        )}
      </div>
    </HeroCard>
  );
}
