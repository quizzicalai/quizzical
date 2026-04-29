import { useEffect, useRef, useState } from 'react';
import { getPlaceholderTopicPool } from '../data/placeholderTopics';

export type UsePlaceholderRotationOptions = {
  /** Pause rotation while true (e.g. when input is focused or has content). */
  paused?: boolean;
  /** Milliseconds between rotations. Defaults to 2200. */
  intervalMs?: number;
  /** Optional fallback shown until the first random pick (e.g. while paused). */
  fallback?: string;
};

/**
 * Returns a placeholder string that rotates through a large pool of
 * personality-quiz noun phrases with a random starting index and a random
 * next pick (never repeating the immediately previous entry).
 *
 * Honours `prefers-reduced-motion: reduce` by holding a single random pick.
 */
export function usePlaceholderRotation(options: UsePlaceholderRotationOptions = {}): string {
  const { paused = false, intervalMs = 2200, fallback } = options;
  const pool = getPlaceholderTopicPool();

  const [current, setCurrent] = useState<string>(() => {
    if (pool.length === 0) return fallback ?? '';
    const start = Math.floor(Math.random() * pool.length);
    return pool[start];
  });

  const lastIndexRef = useRef<number>(-1);

  useEffect(() => {
    if (pool.length <= 1) return;
    if (paused) return;

    const reduceMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion) return;

    const id = window.setInterval(() => {
      let nextIndex = Math.floor(Math.random() * pool.length);
      // Avoid an immediate repeat; one redraw is enough to feel non-sticky.
      if (nextIndex === lastIndexRef.current) {
        nextIndex = (nextIndex + 1) % pool.length;
      }
      lastIndexRef.current = nextIndex;
      setCurrent(pool[nextIndex]);
    }, Math.max(600, intervalMs));

    return () => window.clearInterval(id);
  }, [pool, paused, intervalMs]);

  return current || fallback || '';
}
