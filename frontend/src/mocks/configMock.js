/**
 * Provides comprehensive mock configuration data for local development.
 * This allows the frontend to be developed and tested independently of the BFF.
 */
export const getMockConfig = () => ({
    theme: {
      // Best Practice: Define colors as RGB components for Tailwind opacity modifiers.
      colors: {
        primary: '30 41 59',    // slate-800
        secondary: '100 116 139', // slate-500
        accent: '79 70 229',      // indigo-600
        muted: '226 232 240',     // slate-200
        background: '248 250 252', // slate-50
        white: '255 255 255',
      },
      fonts: {
        body: "'Nunito', sans-serif",
      },
    },
    content: {
      brand: {
        name: 'Quizzical.ai',
      },
      footer: {
        // Best Practice: Dynamically generate the year.
        copyright: `© ${new Date().getFullYear()} Quizzical.ai`,
        navLinks: [
          { text: 'About', href: '/about' },
          { text: 'Terms', href: '/terms' },
          { text: 'Privacy', href: '/privacy' },
        ],
      },
      landingPage: {
        heading: 'Discover Your Inner Persona.',
        inputPlaceholder: "Enter a category like 'Types of Pasta'...",
      },
      notFoundPage: {
        heading: 'Page Not Found',
        subheading: "Oops! The page you were looking for doesn't seem to exist.",
        buttonText: 'Return Home',
      },
      // Add other content keys as needed
    },
  });
  