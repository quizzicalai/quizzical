/* eslint no-console: ["error", { "allow": ["warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

/**
 * We mock @uiball/loaders to a tiny component that exposes the
 * received props via data-* attributes so we can assert them.
 * (size, color, speed are official SuperBalls props.)
 */
vi.mock('@uiball/loaders', () => {
  // keep a module-local to inspect if needed
  let lastProps: any = null;

  const SuperBalls = (props: any) => {
    lastProps = props;
    return (
      <div
        data-testid="mock-superballs"
        data-size={String(props.size)}
        data-speed={String(props.speed)}
        data-color={String(props.color)}
      />
    );
  };

  return { SuperBalls, __getLastProps: () => lastProps };
});

// Helper to import the SUT *after* mocks are in place.
async function importSut() {
  return await import('./WhimsySprite');
}

function getMockNode() {
  return screen.getByTestId('mock-superballs') as HTMLDivElement;
}

describe('WhimsySprite', () => {
  beforeEach(() => {
    cleanup();
    // Clean env before each test
    document.documentElement.style.removeProperty('--color-primary');
    document.documentElement.removeAttribute('data-freeze-loaders');
    delete (window as any).__FREEZE_LOADERS__;
  });

  afterEach(() => {
    cleanup();
    vi.resetModules(); // so useEffect hooks re-run with fresh env
    vi.clearAllMocks();
  });

  it('renders wrapper span with test id and aria-hidden, and mounts SuperBalls', async () => {
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);

    const wrapper = screen.getByTestId('whimsy-sprite');
    expect(wrapper.tagName.toLowerCase()).toBe('span');
    expect(wrapper).toHaveAttribute('aria-hidden', 'true');

    expect(getMockNode()).toBeInTheDocument();
  });

  it('forwards className to the wrapper', async () => {
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite className="foo bar" />);
    expect(screen.getByTestId('whimsy-sprite')).toHaveClass('foo', 'bar');
  });

  it('uses fallback color (#4f46e5) when --color-primary is not set', async () => {
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);
    const mock = getMockNode();
    expect(mock.dataset.color).toBe('#4f46e5');
  });

  it('reads --color-primary if set to an RGB triplet ("79 70 229") and converts to rgb()', async () => {
    document.documentElement.style.setProperty('--color-primary', '79 70 229');
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);
    const mock = getMockNode();
    expect(mock.dataset.color).toBe('rgb(79,70,229)');
  });

  it('accepts any valid CSS color string (e.g. hex)', async () => {
    document.documentElement.style.setProperty('--color-primary', '#ff8800');
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);
    const mock = getMockNode();
    expect(mock.dataset.color?.toLowerCase()).toBe('#ff8800');
  });

  it('defaults to size=40 and speed=1.6 (animating)', async () => {
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);
    const mock = getMockNode();
    expect(parseFloat(mock.dataset.size!)).toBe(40);
    expect(parseFloat(mock.dataset.speed!)).toBeCloseTo(1.6, 5);
  });

  it('pauses (speed=0) when html[data-freeze-loaders] is set', async () => {
    document.documentElement.setAttribute('data-freeze-loaders', '');
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);
    const mock = getMockNode();
    expect(parseFloat(mock.dataset.speed!)).toBe(0);
  });

  it('pauses (speed=0) when window.__FREEZE_LOADERS__ = true', async () => {
    (window as any).__FREEZE_LOADERS__ = true;
    const { WhimsySprite } = await importSut();
    render(<WhimsySprite />);
    const mock = getMockNode();
    expect(parseFloat(mock.dataset.speed!)).toBe(0);
  });
});
