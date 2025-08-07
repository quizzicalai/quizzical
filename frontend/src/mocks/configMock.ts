import { AppConfig } from '../utils/configValidation';

/**
 * Mock application configuration data.
 * This object simulates the payload fetched from the `/config` endpoint
 * and matches the new, validated schema.
 */
export const configData: AppConfig = {
  theme: {
    colors: {
      primary: '79 70 229',
      secondary: '30 41 59',
      accent: '234 179 8',
      bg: '248 250 252',
      fg: '15 23 42',
      border: '226 232 240',
      muted: '100 116 139',
      ring: '129 140 248',
    },
    dark: {
      colors: {
        primary: '129 140 248',
        secondary: '226 232 240',
        accent: '250 204 21',
        bg: '15 23 42',
        fg: '248 250 252',
        border: '30 41 59',
        muted: '148 163 184',
        ring: '129 140 248',
      },
    },
    fonts: {
      sans: 'Inter, sans-serif',
      serif: 'serif',
    },
  },
  content: {
    appName: 'Quizzical AI',
    landingPage: {
      title: 'Unlock Your Inner Persona',
      subtitle: 'Answer a few questions and let our AI reveal a surprising profile of you.',
      placeholder: "e.g., 'Ancient Rome', 'The Marvel Universe', 'Baking'",
      buttonText: 'Create My Quiz',
      validation: {
        minLength: 'Must be at least {min} characters.',
        maxLength: 'Cannot exceed {max} characters.',
      },
    },
    footer: {
      about: { label: 'About', href: '/about' },
      terms: { label: 'Terms of Use', href: '/terms' },
      privacy: { label: 'Privacy', href: '/privacy' },
      donate: { label: 'Donate', href: 'https://github.com/sponsors/YOUR_USERNAME', external: true },
      copyright: 'Quizzical AI',
    },
    loadingStates: {
      quiz: 'Preparing your quiz...',
      question: 'Thinking...',
      page: 'Loading...',
    },
    errors: {
      title: 'An Error Occurred',
      description: 'Something went wrong. Please try again or return to the home page.',
      requestTimeout: "The request timed out. It's taking longer than expected.",
      quizCreationFailed: 'We were unable to create your quiz at this time.',
      categoryNotFound: "Sorry, we couldn't create a quiz for that category. Please try a different one.",
      sessionExpired: 'Your session has expired. Please start a new quiz.',
      resultNotFound: 'This result could not be found. It may have expired or the link may be incorrect.',
      startOver: 'Start Over',
      details: 'Error Details',
      hideDetails: 'Hide Details',
      showDetails: 'Show Details',
      retry: 'Try Again',
      home: 'Go Home',
    },
    aboutPage: {
      title: 'About Quizzical',
      blocks: [
        { type: 'p', text: 'Quizzical is an AI-powered application designed to create unique and engaging quizzes based on any topic you can imagine.' },
        { type: 'p', text: 'This project showcases modern web development practices, including a resilient frontend, a robust API, and dynamic, configuration-driven UI. All the text and styles you see are loaded at runtime.' },
        { type: 'h2', text: 'Our Mission' },
        { type: 'p', text: 'Our mission is to explore the creative possibilities of large language models while building a fun and reliable user experience.' },
      ],
    },
    termsPage: {
      title: 'Terms of Use',
      blocks: [
        { type: 'p', text: 'By using Quizzical (the "Service"), you agree to be bound by these Terms of Use.' },
        { type: 'h2', text: 'Use of the Service' },
        { type: 'p', text: 'The Service is provided for entertainment and informational purposes only. You agree not to use the service for any illegal or unauthorized purpose.' },
        { type: 'h2', text: 'Disclaimer' },
        { type: 'p', text: 'The Service is provided "as is", without warranty of any kind, express or implied.' },
      ],
    },
    privacyPolicyPage: {
      title: 'Privacy Policy',
      blocks: [
        { type: 'p', text: 'Your privacy is important to us. This Privacy Policy explains how we collect, use, and share information about you.' },
        { type: 'h2', text: 'Information We Collect' },
        { type: 'p', text: 'We collect the minimal amount of data necessary to provide the service, including the quiz categories you enter and the answers you select. We do not collect personally identifiable information.' },
      ],
    },
    resultPage: {
      titlePrefix: 'You are',
      shareButton: 'Share Result',
      shareCopied: 'Link Copied!',
      startOverButton: 'Start Another Quiz',
      traitListTitle: 'Key Traits',
      feedback: {
        prompt: 'Was this profile accurate?',
        thumbsUp: 'Thumbs up',
        thumbsDown: 'Thumbs down',
        commentPlaceholder: 'Add an optional comment...',
        submit: 'Submit Feedback',
        thanks: 'Thank you for your feedback!',
      },
      share: {
        socialTitle: 'I discovered my Quizzical profile!',
        socialDescription: 'Find out what your favorite topic says about you. Take a quiz on Quizzical.',
      },
    },
  },
  limits: {
    validation: {
      category_min_length: 3,
      category_max_length: 80,
    },
  },
  // New: Added the apiTimeouts section to the mock config
  apiTimeouts: {
    default: 15000,
    startQuiz: 60000,
    poll: {
      total: 60000,
      interval: 1000,
      maxInterval: 5000,
    },
  },
};
