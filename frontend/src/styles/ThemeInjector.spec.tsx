/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// --- Mock ConfigContext with a controllable config ---
let __currentConfig: any = null;
vi.mock('/src/context/ConfigContext.tsx', () => {
  return {
    // Allow tests to update the config returned from useConfig()
    __setConfig: (c: any) => {
      __currentConfig = c;
    },
    useConfig: () => ({
      config: __currentConfig,
      isLoading: false,
      error: null,
      reload: vi.fn(),
    }),
  };
});

// NOTE: Import after the mock above is installed
const MOD_PATH = '/src/styles/ThemeInjector.tsx';

// Helpers to set/clear CSS variables
const COLOR_VARS = [
  '--color-bg',
  '--color-fg',
  '--color-border',
  '--color-primary',
  '--color-secondary',
  '--color-accent',
  '--color-muted',
  '--color-ring',
  '--color-neutral',
];

function clearThemeVars() {
  const root = document.documentElement;
  for (const v of COLOR_VARS) root.style.removeProperty(v);
  root.style.removeProperty('--font-sans');
  root.style.removeProperty('--font-serif');
  root.style.removeProperty('--font-code');
}

// Access the mock setter from the mocked module
const { __setConfig } = (await import('../context/ConfigContext')) as any;

describe('ThemeInjector', () => {
  beforeEach(() => {
    cleanup();
    clearThemeVars();
    __setConfig(null);
  });

  it('injects mapped color variables and fonts when config.theme is present', async () => {
    const { ThemeInjector } = await import(/* @vite-ignore */ MOD_PATH);

    // Use the real fixture theme
    __setConfig({ theme: CONFIG_FIXTURE.theme });

    render(<ThemeInjector />);

    const root = document.documentElement;

    // Colors are already in "R G B" triplet format in fixture
    expect(root.style.getPropertyValue('--color-primary').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.primary
    );
    expect(root.style.getPropertyValue('--color-secondary').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.secondary
    );
    expect(root.style.getPropertyValue('--color-accent').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.accent
    );
    expect(root.style.getPropertyValue('--color-bg').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.bg
    );
    expect(root.style.getPropertyValue('--color-fg').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.fg
    );
    expect(root.style.getPropertyValue('--color-border').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.border
    );
    expect(root.style.getPropertyValue('--color-muted').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.muted
    );
    expect(root.style.getPropertyValue('--color-ring').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.ring
    );
    expect(root.style.getPropertyValue('--color-neutral').trim()).toBe(
      CONFIG_FIXTURE.theme.colors.neutral
    );

    // Fonts
    expect(root.style.getPropertyValue('--font-sans').trim()).toBe(
      CONFIG_FIXTURE.theme.fonts.sans
    );
    expect(root.style.getPropertyValue('--font-serif').trim()).toBe(
      CONFIG_FIXTURE.theme.fonts.serif
    );
  });

  it('skips unknown color keys and invalid color values; still sets fonts for any keys', async () => {
    const { ThemeInjector } = await import(/* @vite-ignore */ MOD_PATH);

    // primary valid (hex), weird is unmapped, invalid is malformed
    __setConfig({
      theme: {
        colors: {
          primary: '#ffffff', // -> "255 255 255"
          weird: '10 10 10',  // unmapped color key; should be ignored
          invalid: '#gggggg', // invalid hex; should be ignored
        },
        fonts: {
          code: 'Monaco, monospace',
        },
      },
    });

    render(<ThemeInjector />);

    const root = document.documentElement;

    // Primary converted from hex
    expect(root.style.getPropertyValue('--color-primary').trim()).toBe('255 255 255');

    // Unmapped color variables should not exist
    expect(root.style.getPropertyValue('--color-weird').trim()).toBe('');
    expect(root.style.getPropertyValue('--color-invalid').trim()).toBe('');

    // Dynamic font keys are all set (no mapping restriction for fonts)
    expect(root.style.getPropertyValue('--font-code').trim()).toBe('Monaco, monospace');
  });

  it('reacts to theme changes (updates CSS variables on subsequent renders)', async () => {
    const { ThemeInjector } = await import(/* @vite-ignore */ MOD_PATH);

    // Initial theme
    __setConfig({
      theme: {
        colors: { primary: '255 0 0' }, // red
        fonts: { sans: 'Inter, sans-serif' },
      },
    });

    const { rerender } = render(<ThemeInjector />);
    const root = document.documentElement;

    expect(root.style.getPropertyValue('--color-primary').trim()).toBe('255 0 0');
    expect(root.style.getPropertyValue('--font-sans').trim()).toBe('Inter, sans-serif');

    // Update theme to a different primary + font
    __setConfig({
      theme: {
        colors: { primary: '0 0 0' }, // black
        fonts: { sans: 'System UI' },
      },
    });

    // Trigger the effect by re-rendering
    rerender(<ThemeInjector />);

    expect(root.style.getPropertyValue('--color-primary').trim()).toBe('0 0 0');
    expect(root.style.getPropertyValue('--font-sans').trim()).toBe('System UI');
  });

  it('does nothing when config is null/undefined', async () => {
    const { ThemeInjector } = await import(/* @vite-ignore */ MOD_PATH);

    // Pre-set a value to verify it doesn't change
    const root = document.documentElement;
    root.style.setProperty('--color-primary', '1 2 3');
    root.style.setProperty('--font-sans', 'X');

    __setConfig(null);
    render(<ThemeInjector />);

    // Should remain unchanged
    expect(root.style.getPropertyValue('--color-primary').trim()).toBe('1 2 3');
    expect(root.style.getPropertyValue('--font-sans').trim()).toBe('X');
  });
});
