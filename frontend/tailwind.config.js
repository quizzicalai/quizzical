// frontend/tailwind.config.js

/** @type {import('tailwindcss').Config} */

// Numeric RGB fallbacks for each design token. These are baked into the
// generated CSS as the second arg to `var(--name, fallback)`, so every
// `bg-primary`, `text-primary`, `ring-primary/40`, etc. utility renders
// a sensible color even when the matching CSS custom property is unset
// (e.g. before ThemeInjector runs, or when the backend appconfig omits
// the token). Previously a missing var resolved to an invalid color and
// the affected element rendered transparent — which surfaced as a
// white-on-white "Start Quiz" button on the synopsis card. Keep these
// in sync with src/index.css :root defaults and DEFAULT_APP_CONFIG.theme.
const FALLBACK = {
  '--color-primary':       '79 70 229',    // indigo-700
  '--color-secondary':     '30 41 59',     // slate-800
  '--color-accent':        '234 179 8',    // amber-500
  '--color-neutral':       '148 163 184',  // slate-400
  '--color-bg':            '238 242 255',  // indigo-50
  '--color-fg':            '15 23 42',     // slate-900
  '--color-muted':         '148 163 184',  // slate-400
  '--color-border':        '226 232 240',  // slate-200
  '--color-ring':          '129 140 248',  // indigo-400
  '--color-card':          '255 255 255',  // white
  '--color-error':         '220 38 38',    // red-600
  '--color-error-strong':  '185 28 28',    // red-700
  '--color-error-soft':    '254 226 226',  // red-100
  '--color-error-border':  '252 165 165',  // red-300
  '--color-success':       '22 163 74',    // green-600
  '--color-success-soft':  '220 252 231',  // green-100
};

// Helper to generate rgb(var(--color-..., R G B) / <alpha-value>) syntax.
// The numeric fallback ensures the utility always renders a valid color.
const withOpacity = (variableName) => {
  const fb = FALLBACK[variableName];
  const ref = fb ? `var(${variableName}, ${fb})` : `var(${variableName})`;
  return ({ opacityValue }) => {
    if (opacityValue !== undefined) {
      return `rgba(${ref}, ${opacityValue})`;
    }
    return `rgb(${ref})`;
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
        // Semantic status tokens (UX audit H1)
        error: withOpacity('--color-error'),
        'error-strong': withOpacity('--color-error-strong'),
        'error-soft': withOpacity('--color-error-soft'),
        'error-border': withOpacity('--color-error-border'),
        success: withOpacity('--color-success'),
        'success-soft': withOpacity('--color-success-soft'),
      },
      textColor: {
        // Explicit text color mapping
        primary: withOpacity('--color-primary'),
        secondary: withOpacity('--color-secondary'),
        accent: withOpacity('--color-accent'),
        fg: withOpacity('--color-fg'),
        muted: withOpacity('--color-muted'),
        error: withOpacity('--color-error'),
        success: withOpacity('--color-success'),
      },
      fontFamily: {
        sans: ['var(--font-body)', 'ui-sans-serif', 'system-ui'],
        display: ['var(--font-display)', 'ui-sans-serif', 'system-ui'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};