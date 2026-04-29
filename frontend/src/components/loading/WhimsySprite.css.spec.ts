// frontend/src/components/loading/WhimsySprite.css.spec.ts
//
// AC-FE-A11Y-MOTION-2: the global `@media (prefers-reduced-motion: reduce)`
// rule in `src/index.css` MUST exempt the WhimsySprite loader. Otherwise
// users with OS-level "Reduce Motion" enabled see a single static dot
// instead of the dancing dots — the page looks frozen and broken.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('reduced-motion CSS guard (AC-FE-A11Y-MOTION-2)', () => {
  const css = readFileSync(
    resolve(__dirname, '..', '..', 'index.css'),
    'utf-8',
  );

  it('still applies the global reduced-motion override', () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
  });

  it('exempts the WhimsySprite from the reduced-motion override', () => {
    // Selector must mention the whimsy-sprite test id so the SuperBalls
    // ::before keyframes keep running for the user-visible loader.
    expect(css).toMatch(
      /\[data-testid="whimsy-sprite"\][^{]*\*::before[^{]*\{[\s\S]*?animation-duration:\s*revert/,
    );
  });
});
