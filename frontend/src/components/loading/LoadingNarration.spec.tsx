// frontend/src/components/loading/LoadingNarration.spec.tsx
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { act } from 'react-dom/test-utils';
import { LoadingNarration } from './LoadingNarration';

const FAST_LINES = [
  { atMs: 0,   text: 'Thinking…' },
  { atMs: 80,  text: 'Researching topic…' },
  { atMs: 160, text: 'Determining characters…' },
];

describe('LoadingNarration', () => {
  let perfSpy: ReturnType<typeof vi.spyOn>;
  let nowMs = 0;

  beforeEach(() => {
    cleanup();
    vi.useFakeTimers();
    nowMs = 0;
    // Control elapsed time used by the component
    perfSpy = vi.spyOn(performance, 'now').mockImplementation(() => nowMs);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    perfSpy.mockRestore();
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('renders with default a11y and initial text', () => {
    render(<LoadingNarration />);

    // Root region is a polite live region with the default label
    const region = screen.getByRole('status', { name: /loading/i });
    expect(region).toBeInTheDocument();
    expect(region).toHaveAttribute('aria-live', 'polite');

    // Visible text starts at first line
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent('Thinking…');
  });

  it('progresses through lines over time and calls onChangeText only on changes', () => {
    const onChangeText = vi.fn();
    render(<LoadingNarration lines={FAST_LINES} tickMs={10} onChangeText={onChangeText} />);

    // Initial paint (no interval tick yet): shows first line
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent('Thinking…');

    // First interval tick: should fire onChange for the first line
    act(() => { vi.advanceTimersByTime(10); });
    expect(onChangeText).toHaveBeenLastCalledWith('Thinking…');
    expect(onChangeText).toHaveBeenCalledTimes(1);

    // Move time past 80ms and tick: second line
    nowMs = 85;
    act(() => { vi.advanceTimersByTime(10); });
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent('Researching topic…');
    expect(onChangeText).toHaveBeenLastCalledWith('Researching topic…');
    expect(onChangeText).toHaveBeenCalledTimes(2);

    // Move time past 160ms and tick: third line
    nowMs = 170;
    act(() => { vi.advanceTimersByTime(10); });
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent('Determining characters…');
    expect(onChangeText).toHaveBeenLastCalledWith('Determining characters…');
    expect(onChangeText).toHaveBeenCalledTimes(3);

    // Multiple ticks with no time change should NOT call again
    act(() => { vi.advanceTimersByTime(50); });
    expect(onChangeText).toHaveBeenCalledTimes(3);
  });

  it('honors custom ariaLabel', () => {
    render(<LoadingNarration ariaLabel="Processing" />);
    expect(screen.getByRole('status', { name: /processing/i })).toBeInTheDocument();
  });

  it('cleans up its interval on unmount (no extra callbacks after unmount)', () => {
    const onChangeText = vi.fn();
    const { unmount } = render(<LoadingNarration lines={FAST_LINES} tickMs={10} onChangeText={onChangeText} />);

    // One tick to register first callback
    act(() => { vi.advanceTimersByTime(10); });
    expect(onChangeText).toHaveBeenCalledTimes(1);

    // Unmount, then advance timers; no further calls should occur
    unmount();
    nowMs = 5000;
    act(() => { vi.advanceTimersByTime(200); });
    expect(onChangeText).toHaveBeenCalledTimes(1);
  });

  it('uses provided lines immediately for initial text', () => {
    const custom = [
      { atMs: 0, text: 'Booting…' },
      { atMs: 50, text: 'Calibrating…' },
    ];
    render(<LoadingNarration lines={custom} tickMs={10} />);
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent('Booting…');
  });
});
