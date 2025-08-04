// src/styles/ThemeInjector.tsx
import { useEffect } from 'react';
import { useConfig } from '../context/ConfigContext';

/**
 * Converts a hex color string to an RGB triplet string.
 * Example: "#FF5733" -> "255 87 51"
 */
function hexToRgbTriplet(hex: string): string | null {
  if (!hex || typeof hex !== 'string' || !hex.startsWith('#')) {
    return null;
  }
  const h = hex.slice(1);
  const fullHex = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  if (fullHex.length !== 6) {
    return null;
  }
  const r = parseInt(fullHex.slice(0, 2), 16);
  const g = parseInt(fullHex.slice(2, 4), 16);
  const b = parseInt(fullHex.slice(4, 6), 16);
  
  if (isNaN(r) || isNaN(g) || isNaN(b)) {
    return null;
  }

  return `${r} ${g} ${b}`;
}

/**
 * A null-rendering component that reads the theme from the ConfigContext
 * and injects it into the document as CSS variables.
 */
export function ThemeInjector() {
  const { config } = useConfig();

  useEffect(() => {
    if (!config?.theme) return;

    const root = document.documentElement;
    const { colors, fonts } = config.theme;

    // Set color variables
    if (colors) {
      Object.entries(colors).forEach(([key, value]) => {
        const rgbTriplet = hexToRgbTriplet(value);
        if (rgbTriplet) {
          root.style.setProperty(`--color-${key}`, rgbTriplet);
        }
      });
    }

    // Set font variables
    if (fonts) {
      Object.entries(fonts).forEach(([key, value]) => {
        root.style.setProperty(`--font-${key}`, value);
      });
    }
  }, [config?.theme]);

  return null;
}