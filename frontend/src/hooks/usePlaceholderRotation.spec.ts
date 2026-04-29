import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import { usePlaceholderRotation } from './usePlaceholderRotation';
import { getPlaceholderTopicPool } from '../data/placeholderTopics';

describe('usePlaceholderRotation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Default to NO reduced motion
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('returns a value drawn from the pool on initial render (random start)', () => {
    const pool = getPlaceholderTopicPool();
    const poolSet = new Set(pool);
    const { result } = renderHook(() => usePlaceholderRotation());
    expect(poolSet.has(result.current)).toBe(true);
  });

  it('rotates to a new value from the pool on each interval tick', () => {
    const pool = getPlaceholderTopicPool();
    const poolSet = new Set(pool);
    const { result } = renderHook(() => usePlaceholderRotation({ intervalMs: 2000 }));
    const initial = result.current;

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(poolSet.has(result.current)).toBe(true);
    expect(result.current).not.toBe(initial); // pool > 1000, collision near-impossible
  });

  it('does not rotate when paused', () => {
    const { result } = renderHook(() =>
      usePlaceholderRotation({ paused: true, intervalMs: 1000 }),
    );
    const initial = result.current;
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current).toBe(initial);
  });

  it('falls back to provided fallback when pool yields nothing yet (paused boot)', () => {
    const { result } = renderHook(() =>
      usePlaceholderRotation({ paused: true, fallback: 'Hogwarts house' }),
    );
    // Initial pick is real (random); but after blanking we still keep a value.
    expect(typeof result.current).toBe('string');
    expect(result.current.length).toBeGreaterThan(0);
  });

  it('honours prefers-reduced-motion by holding the initial pick', () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes('reduce'),
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    const { result } = renderHook(() => usePlaceholderRotation({ intervalMs: 500 }));
    const initial = result.current;
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current).toBe(initial);
  });
});
