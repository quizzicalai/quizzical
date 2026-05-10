/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

describe('HeroCard', () => {
  const MOD_PATH = '/src/components/layout/HeroCard';

  const load = async () => {
    const mod = await import(MOD_PATH);
    return { HeroCard: mod.HeroCard as React.FC<any> };
  };

  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    cleanup();
    vi.resetModules();
    vi.clearAllMocks();
  });

  it('renders wrapper, card region (with default aria label), content, and children', async () => {
    const { HeroCard } = await load();

    render(
      <HeroCard>
        <div data-testid="child">Hello</div>
      </HeroCard>
    );

    // outer wrapper & card block
    expect(screen.getByTestId('hero-card-wrapper')).toBeInTheDocument();

    // region landmark with default aria-label
    const region = screen.getByRole('region', { name: 'Landing hero card' });
    expect(region).toBeInTheDocument();
    expect(screen.getByTestId('hero-card').className).toContain('hero-surface');

    // inner content and child
    expect(screen.getByTestId('hero-card-content')).toBeInTheDocument();
    expect(screen.getByTestId('child')).toHaveTextContent('Hello');
  });

  it('does not render any decorative mascot or hero artwork', async () => {
    const { HeroCard } = await load();

    render(
      <HeroCard>
        <div>Content</div>
      </HeroCard>
    );

    // No mascot icon, no hero blob, no hero slot — modern minimal layout
    expect(screen.queryByTestId('hero-card-hero')).toBeNull();
    expect(screen.queryByLabelText(/wizard cat/i)).toBeNull();
    expect(document.querySelector('.lp-hero-blob')).toBeNull();
    expect(document.querySelector('.lp-hero')).toBeNull();
  });

  it('forwards a custom ariaLabel to the region landmark', async () => {
    const { HeroCard } = await load();

    render(
      <HeroCard ariaLabel="Quiz hero card">
        <div>Content</div>
      </HeroCard>
    );

    const region = screen.getByRole('region', { name: 'Quiz hero card' });
    expect(region).toBeInTheDocument();
  });

  it('applies className to the outer card and contentClassName to the content wrapper', async () => {
    const { HeroCard } = await load();

    render(
      <HeroCard className="outer-x" contentClassName="inner-y">
        <div>Content</div>
      </HeroCard>
    );

    const card = screen.getByTestId('hero-card');
    const content = screen.getByTestId('hero-card-content');

    expect(card).toHaveClass('outer-x');
    expect(content).toHaveClass('inner-y');
  });

  // UX audit M30: the hero card content wrapper carries the entrance animation
  // class so content fades + slides up on mount.
  it('content wrapper carries the fade-in-up entrance animation class (M30)', async () => {
    const { HeroCard } = await load();

    render(<HeroCard><div>Content</div></HeroCard>);

    const content = screen.getByTestId('hero-card-content');
    expect(content).toHaveClass('animate-fade-in-up');
  });
});
