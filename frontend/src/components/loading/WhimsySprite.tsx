// frontend/src/components/loading/WhimsySprite.tsx
import React from 'react';
import clsx from 'clsx';
import { SuperBalls } from '@uiball/loaders';

/** Reads --color-primary (supports "r g b" or any CSS color) and returns a CSS color string. */
function useThemePrimaryColor(): string {
  const [color, setColor] = React.useState('#4f46e5'); // indigo-600 fallback
  React.useEffect(() => {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue('--color-primary')
      .trim();
    if (!raw) return;
    const triplet = raw.match(/^(\d+)\s+(\d+)\s+(\d+)(?:\s*\/\s*[\d.]+)?$/);
    setColor(triplet ? `rgb(${triplet[1]},${triplet[2]},${triplet[3]})` : raw);
  }, []);
  return color;
}

/** Optional: allow tests/dev tools to freeze the loader without touching OS settings. */
function useFreezeForTests(): boolean {
  const [paused, setPaused] = React.useState(false);
  React.useEffect(() => {
    const root = document.documentElement;
    const compute = () =>
      setPaused(
        root.hasAttribute('data-freeze-loaders') ||
        (window as any).__FREEZE_LOADERS__ === true
      );
    compute();
    const mo = new MutationObserver(compute);
    mo.observe(root, { attributes: true, attributeFilter: ['data-freeze-loaders'] });
    const onToggle = () => compute();
    window.addEventListener('freeze-loaders-toggle', onToggle);
    return () => {
      mo.disconnect();
      window.removeEventListener('freeze-loaders-toggle', onToggle);
    };
  }, []);
  return paused;
}

/**
 * WhimsySprite: tiny brand-coloured loading flourish.
 *
 * - When `spinning` is true (default false), renders the existing
 *   <SuperBalls> animation — used while the system is actively thinking
 *   (submitting topic, preparing quiz, fetching next question).
 * - When `spinning` is false (idle), renders two stationary balls in the
 *   same brand colour: a primary ball and a smaller (50%) + half-opacity
 *   sibling. This matches the resting state requested in the May 25 UX
 *   review ("two stationary balls, one 50% smaller and 50% transparent")
 *   so the sprite reads as decorative at rest and as an animation only
 *   when something is happening.
 * - The freeze test affordance still pauses the active animation.
 */
export function WhimsySprite({
  className,
  spinning = false,
}: {
  className?: string;
  spinning?: boolean;
}) {
  const color = useThemePrimaryColor();
  const paused = useFreezeForTests(); // <-- test/dev only

  return (
    <span
      aria-hidden="true"
      className={clsx('inline-flex items-center justify-center', className)}
      data-testid="whimsy-sprite"
      data-state={spinning ? 'spinning' : 'idle'}
    >
      {spinning ? (
        <SuperBalls size={40} speed={paused ? 0 : 1.6} color={color} />
      ) : (
        <svg
          width={40}
          height={40}
          viewBox="0 0 40 40"
          role="img"
          focusable="false"
          data-testid="whimsy-sprite-idle"
        >
          {/* Primary ball: ~10px diameter (matches a SuperBalls dot). */}
          <circle cx={14} cy={20} r={5} fill={color} />
          {/* Companion ball: 50% smaller (r=2.5) + 50% opacity, brand colour. */}
          <circle cx={26} cy={20} r={2.5} fill={color} fillOpacity={0.5} />
        </svg>
      )}
    </span>
  );
}
