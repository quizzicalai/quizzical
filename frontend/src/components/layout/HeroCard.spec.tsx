/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// Stub the big SVG to keep tests light & deterministic
vi.mock('/src/assets/icons/WizardCatIcon', () => ({
  WizardCatIcon: (props: any) => <svg role="img" data-testid="wizcat" {...props} />,
}));

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

    // inner content and child
    expect(screen.getByTestId('hero-card-content')).toBeInTheDocument();
    expect(screen.getByTestId('child')).toHaveTextContent('Hello');
  });

  it('shows the hero area by default and includes the WizardCatIcon (by label)', async () => {
    const { HeroCard } = await load();

    render(
      <HeroCard>
        <div>Content</div>
      </HeroCard>
    );

    // hero container + icon (label comes from component prop)
    expect(screen.getByTestId('hero-card-hero')).toBeInTheDocument();
    expect(screen.getByLabelText('Wizard cat reading a book')).toBeInTheDocument();
    // or the stub-specific test id if you prefer
    expect(screen.getByTestId('wizcat')).toBeInTheDocument();
  });

  it('hides the hero area when showHero=false', async () => {
    const { HeroCard } = await load();

    render(
      <HeroCard showHero={false}>
        <div>Content</div>
      </HeroCard>
    );

    expect(screen.queryByTestId('hero-card-hero')).toBeNull();
    expect(screen.queryByLabelText('Wizard cat reading a book')).toBeNull();
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
});
