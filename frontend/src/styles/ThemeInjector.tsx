import { useEffect } from 'react';
import { useConfig } from '../context/ConfigContext';
import { toRgbTriplet } from '../utils/color';
import { AppConfig } from '../utils/configValidation';

/**
 * A map to ensure consistency between config keys and CSS variable names.
 */
const THEME_VAR_MAP = {
  bg: 'bg',
  fg: 'fg',
  border: 'border',
  primary: 'primary',
  secondary: 'secondary',
  accent: 'accent',
  muted: 'muted',
  ring: 'ring',
} as const;

/**
 * Injects theme properties as CSS variables into the document root.
 * @param theme - The validated theme configuration object.
 */
function injectTheme(theme: AppConfig['theme']) {
  const root = document.documentElement;

  // Inject light theme colors
  for (const [key, value] of Object.entries(theme.colors || {})) {
    const triplet = typeof value === 'string' ? toRgbTriplet(value) : null;
    const varName = THEME_VAR_MAP[key as keyof typeof THEME_VAR_MAP];
    if (triplet && varName) {
      root.style.setProperty(`--color-${varName}`, triplet);
    }
  }

  // TODO: Implement dark theme injection if supported
  // This could involve checking for a 'dark' class on the root element
  // and setting variables accordingly from `theme.dark.colors`.

  // Inject font variables
  if (theme.fonts) {
    for (const [key, value] of Object.entries(theme.fonts)) {
      if (typeof value === 'string') {
        root.style.setProperty(`--font-${key}`, value);
      }
    }
  }
}

/**
 * A null-rendering component that reads the theme from the ConfigContext
 * and injects it into the document as CSS variables whenever it changes.
 */
export function ThemeInjector() {
  const { config } = useConfig();

  useEffect(() => {
    if (config?.theme) {
      injectTheme(config.theme);
    }
  }, [config?.theme]);

  return null;
}
