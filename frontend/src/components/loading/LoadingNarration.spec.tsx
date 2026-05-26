// frontend/src/components/loading/LoadingNarration.spec.tsx
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { act } from 'react-dom/test-utils';
import { LoadingNarration } from './LoadingNarration';
import { LANDING_PREPARING_LINES } from './LoadingNarration';

const FAST_LINES = [
  { atMs: 0,   text: 'Thinking…' },
  { atMs: 80,  text: 'Researching topic…' },
  { atMs: 160, text: 'Determining personality types…' },
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
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent('Determining personality types…');
    expect(onChangeText).toHaveBeenLastCalledWith('Determining personality types…');
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

  // AC-UX-2026-05-12 — landing "preparing" copy was a single static
  // "Getting things ready" string. It is now a rotating pool of
  // friendly, on-brand lines surfacing the product's range (MBTI /
  // Hogwarts / Famous Elephant). Lock the schedule + first line so
  // copy edits in marketing can't silently break the rotation.
  it('exposes a non-empty LANDING_PREPARING_LINES pool with a 4s+ schedule', () => {
    expect(Array.isArray(LANDING_PREPARING_LINES)).toBe(true);
    expect(LANDING_PREPARING_LINES.length).toBeGreaterThanOrEqual(3);
    // First entry must play immediately and mention the product name.
    const first = LANDING_PREPARING_LINES[0];
    expect(first.atMs).toBe(0);
    expect(first.text.toLowerCase()).toContain('quafel');
    // Subsequent lines must be at least 1s apart so the rotation
    // feels deliberate rather than flickery.
    for (let i = 1; i < LANDING_PREPARING_LINES.length; i++) {
      expect(LANDING_PREPARING_LINES[i].atMs).toBeGreaterThanOrEqual(
        LANDING_PREPARING_LINES[i - 1].atMs + 1000,
      );
    }
  });

  it('rotates LANDING_PREPARING_LINES on schedule', () => {
    const onChangeText = vi.fn();
    nowMs = 0;
    render(
      <LoadingNarration
        lines={LANDING_PREPARING_LINES}
        tickMs={50}
        onChangeText={onChangeText}
      />,
    );

    // First line is rendered synchronously.
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent(
      LANDING_PREPARING_LINES[0].text,
    );

    // Advance past the second line's atMs and tick the interval.
    nowMs = LANDING_PREPARING_LINES[1].atMs + 100;
    act(() => { vi.advanceTimersByTime(60); });
    expect(screen.getByTestId('loading-narration-text')).toHaveTextContent(
      LANDING_PREPARING_LINES[1].text,
    );
    expect(onChangeText).toHaveBeenCalledWith(LANDING_PREPARING_LINES[1].text);
  });
});
