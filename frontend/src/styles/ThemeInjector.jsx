// src/components/theme/ThemeInjector.jsx
import { useEffect, useRef } from 'react';
import { useConfig } from '../../context/ConfigContext';

/**
 * Normalizes various color formats into a space-separated RGB triplet.
 * e.g., "#ff0000" -> "255 0 0"
 * This is required for Tailwind's opacity modifiers.
 * @param {string | number[]} value - The color value to normalize.
 * @returns {string | null} The RGB triplet or null if invalid.
 */
function toRgbTriplet(value) {
  if (!value) return null;
  const s = String(value).trim();

  // From rgb(r, g, b)
  const rgbMatch = s.match(/^rgb[a]?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+).*\)$/i);
  if (rgbMatch) return `${rgbMatch[1]} ${rgbMatch[2]} ${rgbMatch[3]}`;

  // From #rrggbb or #rgb
  if (s.startsWith('#')) {
    const hex = s.slice(1);
    const fullHex = hex.length === 3 ? hex.split('').map(c => c + c).join('') : hex;
    if (fullHex.length === 6) {
      const r = parseInt(fullHex.slice(0, 2), 16);
      const g = parseInt(fullHex.slice(2, 4), 16);
      const b = parseInt(fullHex.slice(4, 6), 16);
      if ([r, g, b].every(n => !isNaN(n))) return `${r} ${g} ${b}`;
    }
  }
  return null; // Return null for invalid formats
}

/**
 * Builds the CSS string of theme variables.
 * @param {object} theme - The theme object from the config.
 * @returns {string} The full CSS string.
 */
function buildCss(theme) {
  const colors = theme?.colors || {};
  const fonts = theme?.fonts || {};

  const lightVars = {
    '--color-primary': toRgbTriplet(colors.primary) ?? '67 56 202', // Default: deep-purple
    '--color-secondary': toRgbTriplet(colors.secondary) ?? '107 114 128', // Default: gray
    '--color-accent': toRgbTriplet(colors.accent) ?? '234 179 8', // Default: amber
    '--color-bg': toRgbTriplet(colors.bg) ?? '255 255 255', // Default: white
    '--color-fg': toRgbTriplet(colors.fg) ?? '17 24 39', // Default: gray-900
    '--color-muted': toRgbTriplet(colors.muted) ?? '156 163 175', // Default: gray-400
    '--font-body': fonts.body ?? 'Inter, ui-sans-serif, system-ui',
    '--font-display': fonts.display ?? 'var(--font-body)',
  };

  const root = Object.entries(lightVars).map(([k, v]) => `${k}: ${v};`).join('\n  ');
  let css = `:root {\n  ${root}\n}\n`;

  if (theme?.dark?.colors) {
    const d = theme.dark.colors;
    const darkVars = {
      '--color-primary': toRgbTriplet(d.primary) ?? lightVars['--color-primary'],
      '--color-secondary': toRgbTriplet(d.secondary) ?? lightVars['--color-secondary'],
      '--color-accent': toRgbTriplet(d.accent) ?? lightVars['--color-accent'],
      '--color-bg': toRgbTriplet(d.bg) ?? '17 24 39', // Default: gray-900
      '--color-fg': toRgbTriplet(d.fg) ?? '249 250 251', // Default: gray-50
      '--color-muted': toRgbTriplet(d.muted) ?? '75 85 99', // Default: gray-600
    };
    const darkBlock = Object.entries(darkVars).map(([k, v]) => `${k}: ${v};`).join('\n  ');
    css += `.dark {\n  ${darkBlock}\n}\n`;
  }

  return css;
}

/**
 * A null-rendering component that injects theme-based CSS variables into the document head.
 */
export function ThemeInjector() {
  const { config } = useConfig();
  const styleRef = useRef(null);

  useEffect(() => {
    if (!config?.theme) return;

    const css = buildCss(config.theme);
    let node = styleRef.current;

    if (!node) {
      node = document.createElement('style');
      node.setAttribute('data-theme-injector', 'quizzical');
      document.head.appendChild(node);
      styleRef.current = node;
    }

    node.textContent = css;

  }, [config?.theme]);

  return null;
}