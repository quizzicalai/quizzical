import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * Regression guard for the stray black-border bug.
 *
 * Tailwind v3's Preflight defaults every element's `border-color` to
 * `currentColor`. The app's text color is near-black slate, so any bare
 * `border` utility (width only, no `border-<color>`) rendered as a black
 * outline — appearing inconsistently across components. We override the
 * default to the subtle brand grey token in `@layer base`. If that override
 * is ever removed, bare borders go black again, so lock it in.
 */
describe('default border color', () => {
  const css = readFileSync(resolve(__dirname, 'index.css'), 'utf8').replace(/\s+/g, ' ');

  it('defaults *,::before,::after border-color to the --color-border token', () => {
    expect(css).toContain(
      '@layer base { *, ::before, ::after { border-color: rgb(var(--color-border',
    );
  });

  it('does not leave the global border default as currentColor', () => {
    expect(css).not.toContain('::after { border-color: currentColor');
  });
});
