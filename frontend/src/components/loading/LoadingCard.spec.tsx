// frontend/src/components/loading/LoadingCard.spec.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// --- Mocks -------------------------------------------------------------------
// Keep the wrapper landmark but simplify the markup.
vi.mock('../layout/HeroCard', () => ({
  HeroCard: (props: any) => (
    <section
      role="region"
      aria-label={props.ariaLabel}
      data-testid="hero-card-mock"
    >
      {props.children}
    </section>
  ),
}));

// Avoid pulling in the real loader implementation/animation.
vi.mock('./WhimsySprite', () => ({
  WhimsySprite: () => <div data-testid="whimsy-sprite">Sprite</div>,
}));

// Avoid the internal interval/timer; render stable content.
vi.mock('./LoadingNarration', () => ({
  LoadingNarration: () => (
    <div role="status" data-testid="loading-narration">
      Thinking…
    </div>
  ),
}));

// --- SUT ---------------------------------------------------------------------
import { LoadingCard } from './LoadingCard';

describe('LoadingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.resetModules();
  });

  it('renders inside a region landmark with aria-label "Loading card"', () => {
    render(<LoadingCard />);

    const region = screen.getByRole('region', { name: /loading card/i });
    expect(region).toBeInTheDocument();
    // sanity: our simplified HeroCard mock should be what rendered it
    expect(screen.getByTestId('hero-card-mock')).toBe(region);
  });

  it('includes the loader sprite and the narration text', () => {
    render(<LoadingCard />);

    expect(screen.getByTestId('whimsy-sprite')).toBeInTheDocument();
    const narration = screen.getByTestId('loading-narration');
    expect(narration).toBeInTheDocument();
    expect(narration).toHaveAttribute('role', 'status');
  });

  it('keeps the intended inline layout wrappers', () => {
    const { container } = render(<LoadingCard />);
    // Outer flex wrapper
    const outer = container.querySelector('.flex.items-center.justify-center');
    expect(outer).not.toBeNull();

    // Inner inline-flex strip with spacing
    const strip = container.querySelector('.inline-flex.items-center.gap-3');
    expect(strip).not.toBeNull();
  });
});
