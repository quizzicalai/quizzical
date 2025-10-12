// frontend/tailwind.config.js

/** @type {import('tailwindcss').Config} */

// Helper to generate rgb(var(--color-...) / <alpha-value>) syntax
const withOpacity = (variableName) => {
  return ({ opacityValue }) => {
    if (opacityValue !== undefined) {
      return `rgba(var(${variableName}), ${opacityValue})`;
    }
    return `rgb(var(${variableName}))`;
  };
};

export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
    './playwright/**/*.{html,ts,tsx}',
    './tests/ct/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Map semantic names to CSS variables
        primary: withOpacity('--color-primary'),
        secondary: withOpacity('--color-secondary'),
        accent: withOpacity('--color-accent'),
        neutral: withOpacity('--color-neutral'),
        bg: withOpacity('--color-bg'),
        fg: withOpacity('--color-fg'),
        muted: withOpacity('--color-muted'),
        border: withOpacity('--color-border'),
        ring: withOpacity('--color-ring'),
        card: withOpacity('--color-card'),
      },
      textColor: {
        // Explicit text color mapping
        primary: withOpacity('--color-primary'),
        secondary: withOpacity('--color-secondary'),
        accent: withOpacity('--color-accent'),
        fg: withOpacity('--color-fg'),
        muted: withOpacity('--color-muted'),
      },
      fontFamily: {
        sans: ['var(--font-body)', 'ui-sans-serif', 'system-ui'],
        display: ['var(--font-display)', 'ui-sans-serif', 'system-ui'],
      },
    },
  },
  plugins: [],
};