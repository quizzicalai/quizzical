/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render } from '@testing-library/react';
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// ---------- Hoisted mock for ConfigContext ----------
// Define state & setters at hoist-time so the mock factory can reference them.
const { __getConfig, __setConfig } = vi.hoisted(() => {
  let _cfg: any = null;
  return {
    __getConfig: () => _cfg,
    __setConfig: (c: any) => { _cfg = c; },
  };
});

// IMPORTANT: literal path so Vitest hoists correctly and matches the module under test.
vi.mock('../context/ConfigContext', () => ({
  __setConfig,
  useConfig: () => ({
    config: __getConfig(),
    isLoading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

// Import AFTER the mock so the SUT reads the mocked module.
import { ThemeInjector, computeThemeVars } from './ThemeInjector';

// ---------- Helpers ----------
function resetRootStyles() {
  document.documentElement.removeAttribute('style');
}

// ---------- Tests ----------
describe('computeThemeVars (pure mapping)', () => {
  beforeEach(() => {
    resetRootStyles();
  });

  it('maps colors via toRgbTriplet, ignores unknown/invalid, and sets font aliases/title', () => {
    const vars = computeThemeVars({
      colors: {
        primary: '#ffffff',            // -> 255 255 255
        card: '250 250 250',
        weird: '10 10 10',             // unknown key -> ignored
        invalid: '#gggggg',            // invalid hex -> ignored
      },
      fonts: {
        sans: 'Inter, sans-serif',
        serif: 'Georgia, serif',
        code: 'Monaco, monospace',
      },
      fontSizes: {
        landingTitle: '2.5rem',
        button: '1rem',
        body: '1rem',
      },
      // layout typing is permissive at runtime; this mirrors the component behavior
      layout: { landing: { heroHeight: '60vh', titleMaxWidth: '42rem' } },
    } as any);

    // Colors
    expect(vars['--color-primary']).toBe('255 255 255');
    expect(vars['--color-card']).toBe('250 250 250');
    expect(vars['--color-weird']).toBeUndefined();
    expect(vars['--color-invalid']).toBeUndefined();

    // Fonts + aliases
    expect(vars['--font-sans']).toBe('Inter, sans-serif');
    expect(vars['--font-serif']).toBe('Georgia, serif');
    expect(vars['--font-code']).toBe('Monaco, monospace');
    expect(vars['--font-body']).toBe('Inter, sans-serif');   // alias
    expect(vars['--font-display']).toBe('Georgia, serif');   // alias
    expect(vars['--font-title']).toBe('Georgia, serif');     // alias

    // Font sizes (camelCase -> kebab-case)
    expect(vars['--font-size-landing-title']).toBe('2.5rem');
    expect(vars['--font-size-button']).toBe('1rem');
    expect(vars['--font-size-body']).toBe('1rem');

    // Landing layout tokens
    expect(vars['--lp-hero-height']).toBe('60vh');
    expect(vars['--lp-title-max-width']).toBe('42rem');
  });

  it('returns empty object when theme is undefined', () => {
    expect(computeThemeVars(undefined)).toEqual({});
  });
});

describe('ThemeInjector (React side-effects)', () => {
  beforeEach(() => {
    __setConfig(null);
    resetRootStyles();
    vi.restoreAllMocks();
  });

  it('injects variables from fixture theme on mount', () => {
    __setConfig({ theme: CONFIG_FIXTURE.theme });
    render(<ThemeInjector />);

    const root = document.documentElement;
    const expected = computeThemeVars(CONFIG_FIXTURE.theme);

    // Spot-check a few sentinel vars; exhaustive mapping is tested above.
    expect(root.style.getPropertyValue('--color-primary').trim())
      .toBe(expected['--color-primary']);
    expect(root.style.getPropertyValue('--color-bg').trim())
      .toBe(expected['--color-bg']);
    expect(root.style.getPropertyValue('--font-sans').trim())
      .toBe(expected['--font-sans']);
    expect(root.style.getPropertyValue('--font-body').trim())
      .toBe(expected['--font-body']);
    expect(root.style.getPropertyValue('--font-display').trim())
      .toBe(expected['--font-display']);
    // May be undefined in some fixtures; trim() on empty string is safe
    expect(root.style.getPropertyValue('--font-title').trim())
      .toBe((expected['--font-title'] ?? '').trim());
  });

  it('updates variables on theme change (rerender)', () => {
    __setConfig({
      theme: {
        colors: { primary: '#ff0000' },
        fonts: { sans: 'Inter' },
        fontSizes: { button: '0.875rem' },
      },
    });

    const { rerender } = render(<ThemeInjector />);
    const first = computeThemeVars(__getConfig().theme);

    expect(document.documentElement.style.getPropertyValue('--color-primary').trim())
      .toBe(first['--color-primary']);
    expect(document.documentElement.style.getPropertyValue('--font-sans').trim())
      .toBe(first['--font-sans']);
    expect(document.documentElement.style.getPropertyValue('--font-size-button').trim())
      .toBe(first['--font-size-button']);

    __setConfig({
      theme: {
        colors: { primary: '#000000' },
        fonts: { sans: 'System UI' },
        fontSizes: { button: '1rem' },
      },
    });
    rerender(<ThemeInjector />);

    const second = computeThemeVars(__getConfig().theme);
    expect(document.documentElement.style.getPropertyValue('--color-primary').trim())
      .toBe(second['--color-primary']);
    expect(document.documentElement.style.getPropertyValue('--font-sans').trim())
      .toBe(second['--font-sans']);
    expect(document.documentElement.style.getPropertyValue('--font-size-button').trim())
      .toBe(second['--font-size-button']);
  });

  it('does nothing when config/theme is missing', () => {
    const setSpy = vi.spyOn(document.documentElement.style, 'setProperty');

    __setConfig(null);
    render(<ThemeInjector />);
    expect(setSpy).not.toHaveBeenCalled();

    setSpy.mockClear();
    __setConfig({ theme: undefined });
    render(<ThemeInjector />);
    expect(setSpy).not.toHaveBeenCalled();
  });
});
