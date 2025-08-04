// src/mocks/configMock.js

/**
 * Mock application configuration data.
 * This object simulates the payload fetched from the `/config` endpoint
 * and matches the new, validated schema.
 */
export const configData = {
  // THEME: Restructured with nested objects and simple keys
  theme: {
    colors: {
      bg: "#FFFFFF",
      fg: "#1F2937", // Gray 800
      primary: "#4A90E2",
      secondary: "#50E3C2",
      muted: "#9CA3AF", // Gray 400
      border: "#E5E7EB", // Gray 200
      ring: "#4A90E2",
    },
    fonts: {
      body: "'Inter', ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, 'Apple Color Emoji', 'Segoe UI Emoji'",
      display: "'Inter', ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, 'Apple Color Emoji', 'Segoe UI Emoji'",
    },
    dark: {
        colors: {
            bg: "#111827", // Gray 900
            fg: "#F9FAFB", // Gray 50
            primary: "#60A5FA", // Blue 400
            secondary: "#34D399", // Emerald 400
            muted: "#6B7280", // Gray 500
            border: "#374151", // Gray 700
            ring: "#60A5FA",
        }
    }
  },
  // CONTENT: Expanded to include all required text and structured data
  content: {
    appName: "Quizzical",
    landingPage: {
      title: "Unlock Your Inner Persona",
      subtitle: "Answer a few questions and let our AI reveal a surprising profile of you.",
      inputPlaceholder: "e.g., 'Ancient Rome', 'The Marvel Universe', 'Baking'",
      submitButton: "Create My Quiz",
      inputAriaLabel: "Enter a quiz category",
    },
    // FOOTER: Updated to the new {label, href, external} object structure
    footer: {
      about:   { label: "About",        href: "/about",   external: false },
      terms:   { label: "Terms of Use", "href": "/terms",   external: false },
      privacy: { label: "Privacy",      href: "/privacy", "external": false },
      donate:  { label: "Donate",       href: "https://github.com/sponsors/YOUR_USERNAME", external: true },
      copyright: "Quizzical AI",
    },
    // STATIC PAGES: New content for About, Terms, and Privacy pages
    aboutPage: {
      title: "About Quizzical",
      blocks: [
        { type: "p", text: "Quizzical is an AI-powered application designed to create unique and engaging quizzes based on any topic you can imagine." },
        { type: "p", text: "This project showcases modern web development practices, including a resilient frontend, a robust API, and dynamic, configuration-driven UI. All the text and styles you see are loaded at runtime." },
        { type: "h2", text: "Our Mission" },
        { type: "p", text: "Our mission is to explore the creative possibilities of large language models while building a fun and reliable user experience." }
      ]
    },
    termsPage: {
      title: "Terms of Use",
      blocks: [
        { type: "p", text: "By using Quizzical (the \"Service\"), you agree to be bound by these Terms of Use." },
        { type: "h2", text: "Use of the Service" },
        { type: "p", text: "The Service is provided for entertainment and informational purposes only. You agree not to use the service for any illegal or unauthorized purpose." },
        { type: "h2", text: "Disclaimer"},
        { type: "p", text: "The Service is provided \"as is\", without warranty of any kind, express or implied."}
      ]
    },
    privacyPolicyPage: {
      title: "Privacy Policy",
      blocks: [
        { type: "p", text: "Your privacy is important to us. This Privacy Policy explains how we collect, use, and share information about you." },
        { type: "h2", text: "Information We Collect" },
        { type: "p", text: "We collect the minimal amount of data necessary to provide the service, including the quiz categories you enter and the answers you select. We do not collect personally identifiable information." }
      ]
    },
    // RESULT PAGE: Expanded with all necessary labels
    resultPage: {
      titlePrefix: "You are",
      shareButton: "Share Result",
      shareCopied: "Link Copied!",
      startOverButton: "Start Another Quiz",
      traitListTitle: "Key Traits",
      feedback: {
        prompt: "Was this profile accurate?",
        thumbsUp: "Thumbs up",
        thumbsDown: "Thumbs down",
        commentPlaceholder: "Add an optional comment...",
        submit: "Submit Feedback",
        thanks: "Thank you for your feedback!",
      },
      share: {
        socialTitle: "I discovered my Quizzical profile!",
        socialDescription: "Find out what your favorite topic says about you. Take a quiz on Quizzical."
      },
    },
    // ERRORS: New section for all user-facing error messages
    errors: {
      title: "An Error Occurred",
      description: "Something went wrong. Please try again or return to the home page.",
      retry: "Try Again",
      home: "Go Home",
      startOver: "Start Over",
      categoryNotFound: "Sorry, we couldn't create a quiz for that category. Please try a different one.",
      requestTimeout: "The request timed out. It's taking longer than expected.",
      quizCreationFailed: "We were unable to create your quiz at this time.",
      resultNotFound: "This result could not be found. It may have expired or the link may be incorrect."
    },
  },
  limits: {
    validation: {
      category_min_length: 3,
      category_max_length: 80,
    },
  },
};