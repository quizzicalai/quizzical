// src/config/defaultAppConfig.ts
import type { AppConfig } from '../utils/configValidation'

/**
 * Minimal, schema-compatible default config for local dev
 * when VITE_USE_MOCK_CONFIG === 'true'. Keep it small and stable.
 */
export const DEFAULT_APP_CONFIG: AppConfig = {
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
      neutral: '148 163 184',
    },
    fonts: { sans: 'Inter, sans-serif', serif: 'serif' },
  },
  content: {
    appName: 'Quizzical AI',
    landingPage: {
      title: 'Unlock Your Inner Persona',
      subtitle:
        'Answer a few questions and let our AI reveal a surprising profile of you.',
      placeholder: "e.g., 'Ancient Rome'",
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
      donate: {
        label: 'Donate',
        href: 'https://github.com/sponsors/your',
        external: true,
      },
      // if your type includes this, keep; otherwise omit
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
        resultNotFound: 'This result could not be found. It may have expired or the link may be incorrect.',
        startOver: 'Start Over',
        retry: 'Try Again',
        home: 'Go Home',
    },
    aboutPage: { title: 'About Quizzical', blocks: [{ type: 'p', text: 'About body' }] as any },
    termsPage: { title: 'Terms of Use', blocks: [{ type: 'p', text: 'Terms body' }] as any },
    privacyPolicyPage: { title: 'Privacy Policy', blocks: [{ type: 'p', text: 'Privacy body' }] as any },
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
        socialDescription: 'Try Quizzical.',
      },
    },
  },
  limits: { validation: { category_min_length: 3, category_max_length: 80 } },
  apiTimeouts: {
    default: 15000,
    startQuiz: 60000,
    poll: { total: 60000, interval: 1000, maxInterval: 5000 },
  },
}
