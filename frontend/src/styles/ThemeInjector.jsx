import React, { useMemo } from 'react';
import { useConfig } from '../context/ConfigContext';

/**
 * A robust, recursive helper function to flatten a theme object and
 * generate CSS custom property strings.
 * @param {object} obj - The theme object (or a nested part of it).
 * @param {string} prefix - The current prefix for the CSS variable names.
 * @returns {string} A string of CSS variables.
 */
const generateCssVariables = (obj, prefix = '') => {
  if (!obj) return '';
  return Object.entries(obj)
    .map(([key, value]) => {
      const newKey = prefix ? `${prefix}-${key}` : key;
      if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
        // If the value is an object, recurse.
        return generateCssVariables(value, newKey);
      }
      // Otherwise, create the CSS variable.
      return `--${newKey}: ${value};`;
    })
    .join('\n');
};

/**
 * A React component that injects the fetched theme configuration
 * as CSS variables into the document's head.
 */
export function ThemeInjector() {
  const config = useConfig();
  
  const cssVariables = useMemo(() => {
    if (!config || !config.theme) return '';
    const rootVariables = generateCssVariables(config.theme);
    return `:root { ${rootVariables} }`;
  }, [config]);

  if (!cssVariables) {
    return null;
  }

  return <style>{cssVariables}</style>;
}
