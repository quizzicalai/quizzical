/** @type {import('tailwindcss').Config} */
export default {
    content: [
      "./index.html",
      "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
      extend: {
        // This section maps our dynamic CSS variables to Tailwind classes.
        // Now you can use classes like `bg-primary`, `text-accent`, etc.
        colors: {
          primary: 'rgb(var(--color-primary) / <alpha-value>)',
          secondary: 'rgb(var(--color-secondary) / <alpha-value>)',
          accent: 'rgb(var(--color-accent) / <alpha-value>)',
          muted: 'rgb(var(--color-muted) / <alpha-value>)',
          background: 'rgb(var(--color-background) / <alpha-value>)',
          white: 'rgb(var(--color-white) / <alpha-value>)',
        },
        fontFamily: {
          // This sets the default 'sans' font to our custom font variable.
          sans: ['var(--font-body)', 'sans-serif'],
        },
      },
    },
    plugins: [],
  }
  