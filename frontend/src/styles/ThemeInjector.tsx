import { useEffect } from 'react';
import { useConfig } from '../context/ConfigContext';
import { toRgbTriplet } from '../utils/color';
import type { AppConfig } from '../types/config';

const THEME_VAR_MAP = {
  bg: 'bg',
  fg: 'fg',
  border: 'border',
  primary: 'primary',
  card: 'card',
  secondary: 'secondary',
  accent: 'accent',
  muted: 'muted',
  ring: 'ring',
  neutral: 'neutral',
} as const;

function toKebabCase(key: string) {
  return key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
}

/**
 * Pure function: given a theme object, produce the CSS var map (name -> value).
 * Exported for precise, fast unit tests. Does not touch the DOM or have side-effects.
 */
export function computeThemeVars(theme: AppConfig['theme'] | undefined) {
  const out: Record<string, string> = {};
  if (!theme) return out;

  // Colors → --color-*
  const colors = theme.colors ?? {};
  for (const [key, value] of Object.entries(colors)) {
    const varName = THEME_VAR_MAP[key as keyof typeof THEME_VAR_MAP];
    if (!varName || typeof value !== 'string') continue;
    const triplet = toRgbTriplet(value);
    if (triplet) out[`--color-${varName}`] = triplet;
  }

  // Fonts → --font-*
  const fonts = theme.fonts ?? {};
  for (const [k, v] of Object.entries(fonts)) {
    if (typeof v === 'string') out[`--font-${k}`] = v;
  }
  if (typeof fonts.sans === 'string') out['--font-body'] = fonts.sans;
  if (typeof fonts.serif === 'string') {
    out['--font-display'] = fonts.serif;
    // NEW: title font used by .lp-title (kept as in original)
    out['--font-title'] = fonts.serif;
  }

  // Font sizes → --font-size-*
  const fontSizes = theme.fontSizes ?? {};
  for (const [k, v] of Object.entries(fontSizes)) {
    if (typeof v === 'string') out[`--font-size-${toKebabCase(k)}`] = v;
  }

  // Landing layout tokens → --lp-*
  const lp = (theme as any)?.layout?.landing as Record<string, string> | undefined;
  if (lp) {
    for (const [k, v] of Object.entries(lp)) {
      if (typeof v === 'string') out[`--lp-${toKebabCase(k)}`] = v;
    }
  }

  return out;
}

function injectTheme(theme: AppConfig['theme']) {
  const root = document.documentElement;
  const vars = computeThemeVars(theme);
  for (const [name, val] of Object.entries(vars)) {
    root.style.setProperty(name, val);
  }
}

export function ThemeInjector() {
  const { config } = useConfig();
  useEffect(() => {
    if (config?.theme) injectTheme(config.theme);
  }, [config?.theme]);
  return null;
}
