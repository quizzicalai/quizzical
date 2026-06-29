import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * Regression guard for the stray-black-border root cause.
 *
 * The theme colors are CSS vars holding SPACE-separated channels
 * ("226 232 240"). The `withOpacity` helper in tailwind.config.js MUST emit
 * modern slash syntax — `rgb(R G B / A)` — for the opacity case. The legacy
 * `rgba(R G B, A)` form is INVALID with space-separated channels, so the
 * browser drops the whole declaration and every `border-<color>` (and any
 * `/opacity` color) falls back to Tailwind Preflight's `border-color:
 * currentColor` (= near-black text) — i.e. stray black borders across the app.
 * Lock the slash form in.
 */
describe('tailwind.config withOpacity color syntax', () => {
  const cfg = readFileSync(resolve(__dirname, '..', 'tailwind.config.js'), 'utf8');

  it('emits modern rgb(R G B / A) slash syntax for the opacity case', () => {
    expect(cfg).toMatch(/rgb\(\$\{ref\} \/ \$\{opacityValue\}\)/);
  });

  it('never emits the invalid legacy rgba(R G B, A) comma form', () => {
    expect(cfg).not.toMatch(/rgba\(\$\{ref\},/);
  });
});
