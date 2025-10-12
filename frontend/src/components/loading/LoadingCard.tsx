// frontend/src/components/loading/LoadingCard.tsx
import React from 'react';
import { HeroCard } from '../layout/HeroCard';
import { WhimsySprite } from './WhimsySprite';
import { LoadingNarration } from './LoadingNarration';
import type { LoadingNarrationProps } from './LoadingNarration';

/**
 * A centered loading strip placed inside the standard hero card.
 * Reuses Phase 0 HeroCard so layout/spacing/hero are identical.
 */
export function LoadingCard({ lines }: { lines?: LoadingNarrationProps['lines'] }) {
  return (
    <HeroCard ariaLabel="Loading card">
      <div className="flex items-center justify-center">
        <div className="inline-flex items-center gap-3">
          <WhimsySprite />
          <LoadingNarration lines={lines} />
        </div>
      </div>
    </HeroCard>
  );
}
