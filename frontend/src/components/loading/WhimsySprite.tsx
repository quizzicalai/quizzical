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
 * WhimsySprite (SuperBalls): always animates (ignores OS reduced-motion).
 * You can pause in tests by setting: document.documentElement.setAttribute('data-freeze-loaders','')
 * or window.__FREEZE_LOADERS__ = true
 */
export function WhimsySprite({ className }: { className?: string }) {
  const color = useThemePrimaryColor();
  const paused = useFreezeForTests(); // <-- test/dev only

  return (
    <span
      aria-hidden="true"
      className={clsx('inline-flex items-center justify-center', className)}
      data-testid="whimsy-sprite"
    >
      <SuperBalls size={40} speed={paused ? 0 : 1.6} color={color} />
    </span>
  );
}
