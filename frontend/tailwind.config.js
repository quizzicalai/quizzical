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
  '--color-primary':         '79 70 229',    // indigo-700
  '--color-secondary':       '30 41 59',     // slate-800
  '--color-compliment':      '0 121 174',    // sea-blue accent (#0079AE)
  // A3 (UI-REVIEW-2026-06-29): amber-500 (1.92:1 on white — fails even
  // large-text 3:1 on the error/404 headings) → amber-600 (3.19:1, passes
  // large-text 3:1). Reserve amber for large/non-text only.
  '--color-accent':          '217 119 6',    // amber-600
  '--color-neutral':         '148 163 184',  // slate-400
  '--color-bg':              '248 250 252',  // slate-50 (was indigo-50)
  '--color-fg':              '15 23 42',     // slate-900
  '--color-muted':           '148 163 184',  // slate-400
  // A1 — kept in sync with index.css/defaultAppConfig; consumed via raw CSS
  // var() in .lp-subtitle (no Tailwind utility is exposed to avoid colliding
  // with the existing `text-secondary` = --color-secondary utility).
  '--color-text-secondary':  '71 85 105',    // slate-600 (AA body/secondary)
  '--color-border':          '226 232 240',  // slate-200
  '--color-ring':            '129 140 248',  // indigo-400
  '--color-card':            '255 255 255',  // white
  '--color-error':           '220 38 38',    // red-600
  '--color-error-strong':    '185 28 28',    // red-700
  '--color-error-soft':      '254 226 226',  // red-100
  '--color-error-border':    '252 165 165',  // red-300
  // Kept in sync with src/index.css :root (was green-600 here vs green-700
  // there — they disagreed; both are now green-700).
  '--color-success':         '21 128 61',    // green-700
  '--color-success-soft':    '220 252 231',  // green-100
  '--color-success-border':  '134 239 172',  // green-300
  '--color-success-bg':      '240 253 244',  // green-50
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
        compliment: withOpacity('--color-compliment'),
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
        'success-border': withOpacity('--color-success-border'),
        'success-bg': withOpacity('--color-success-bg'),
      },
      textColor: {
        // Explicit text color mapping
        primary: withOpacity('--color-primary'),
        secondary: withOpacity('--color-secondary'),
        compliment: withOpacity('--color-compliment'),
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
      // ---- Design-token scales (UI-REVIEW-2026-06-29, SAFE-TO-APPLY) ----
      // Additive only — these introduce new utilities mapped to the CSS-var
      // scales defined in src/index.css :root. They DO NOT change Tailwind's
      // built-in scale, so existing `rounded-*`, `shadow-*`, `p-*`,
      // `duration-*`, `text-*` utilities render exactly as before.
      borderRadius: {
        'token-sm': 'var(--radius-sm, 0.5rem)',
        'token-md': 'var(--radius-md, 0.75rem)',
        'token-lg': 'var(--radius-lg, 1rem)',
        'token-xl': 'var(--radius-xl, 1.25rem)',
        'token-pill': 'var(--radius-pill, 9999px)',
      },
      boxShadow: {
        'token-1': 'var(--shadow-1)',
        'token-2': 'var(--shadow-2)',
        'token-3': 'var(--shadow-3)',
      },
      transitionDuration: {
        fast: 'var(--dur-fast, 120ms)',
        base: 'var(--dur-base, 180ms)',
        slow: 'var(--dur-slow, 220ms)',
      },
      // UX-MOTION-2026-06-29 — easing tokens surfaced as utilities
      // (`ease-out-token`, `ease-standard`) so component-level Tailwind
      // transitions can share the same curves as the hand-written .lp-* rules.
      // `ease-out` is a Tailwind built-in; we namespace ours to avoid clobbering
      // it while still routing through the CSS-var token.
      transitionTimingFunction: {
        'out-token': 'var(--ease-out, cubic-bezier(0, 0, 0.2, 1))',
        standard: 'var(--ease-standard, cubic-bezier(0.4, 0, 0.2, 1))',
      },
      fontSize: {
        'token-xs': 'var(--font-size-xs, 0.8rem)',
        'token-sm': 'var(--font-size-sm, 0.875rem)',
        'token-base': 'var(--font-size-base, 1rem)',
        'token-lg': 'var(--font-size-lg, 1.25rem)',
        'token-xl': 'var(--font-size-xl, 1.563rem)',
        'token-2xl': 'var(--font-size-2xl, 1.953rem)',
        'token-3xl': 'var(--font-size-3xl, 2.441rem)',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};