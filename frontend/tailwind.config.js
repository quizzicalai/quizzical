/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class', // Enable class-based dark mode
  content: [
    './index.html',
    './src/**/*.{js,jsx,ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Use the CSS variables with a slash for opacity alpha value
        primary: 'rgb(var(--color-primary) / <alpha-value>)',
        secondary: 'rgb(var(--color-secondary) / <alpha-value>)',
        accent: 'rgb(var(--color-accent) / <alpha-value>)',
        bg: 'rgb(var(--color-bg) / <alpha-value>)',
        fg: 'rgb(var(--color-fg) / <alpha-value>)',
        muted: 'rgb(var(--color-muted) / <alpha-value>)',
      },
      fontFamily: {
        // Use the CSS variables with safe fallbacks
        sans: ['var(--font-body)', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica', 'Arial'],
        display: ['var(--font-display)', 'var(--font-body)', 'ui-sans-serif', 'system-ui'],
      },
      borderRadius: {
        // Optional: Make border radius themeable as well
        DEFAULT: 'var(--radius, 0.5rem)',
        lg: 'var(--radius-lg, 0.75rem)',
      },
    },
  },
  plugins: [],
};