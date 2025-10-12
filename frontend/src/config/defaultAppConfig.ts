// frontend/src/config/defaultAppConfig.ts
import type { AppConfig } from '../types/config';

export const DEFAULT_APP_CONFIG: AppConfig = {
  theme: {
    colors: {
      primary: '79 70 229',
      secondary: '30 41 59',
      accent: '234 179 8',
      bg: '238 242 255',
      card: '255 255 255',
      fg: '15 23 42',
      border: '226 232 240',
      muted: '148 163 184', // softer default
      ring: '129 140 248',
      neutral: '148 163 184',
    },
    fonts: {
      sans: 'Inter, sans-serif',
      serif: 'Nunito, sans-serif',
    },
    fontSizes: {
      body: '1rem',
      input: '1rem',
      button: '1rem',
      landingTitle: '2.25rem',
      landingSubtitle: '1.125rem',
    },

    // NEW: layout tokens for the landing page (all CSS strings with units)
    layout: {
      landing: {
        pagePtSm: '1.5rem',
        pagePtMd: '3rem',
        pagePtLg: '4rem',

        cardMaxW: '56rem',       // ~max-w-3xl
        cardPadSm: '2rem',       // ~p-8
        cardPadMd: '3rem',       // ~p-12
        cardPadLg: '4rem',       // ~p-16
        cardRadius: '1rem',      // ~rounded-2xl
        cardShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',

        heroHSm: '6rem',         // 96px
        heroHMd: '7rem',         // 112px
        heroHLg: '8rem',         // 128px

        spaceAfterHeroSm: '1.5rem',
        spaceAfterHeroMd: '2rem',
        titleMaxW: '42rem',
        subtitleMaxW: '38rem',

        spaceTitleToSubtitleSm: '1rem',
        spaceTitleToSubtitleMd: '1.25rem',
        spaceSubtitleToFormSm: '2.5rem',
        spaceSubtitleToFormMd: '3rem',

        formMaxW: '36rem',

        inputHeight: '2.5rem',   // 40px internal input
        pillGap: '0.75rem',      // 12px
        pillPl: '1.25rem',       // 20px
        pillPad: '0.375rem',     // p-1.5
        pillBorder: '1px solid rgba(var(--color-muted), 0.55)', // use muted, not border
        pillBg: 'rgba(var(--color-card), 0.9)',
        ringAlpha: '0.2',

        submitSize: '2.5rem',    // 40px perfect circle

        blobSizeSm: '6rem',
        blobSizeMd: '7rem',
        blobSizeLg: '8rem',
        blobOpacity: '0.18',                // very subtle
        underlineWidth: '8rem',             // ~128px
        underlineHeight: '6px',
        underlineRadius: '9999px',
      },
    },
  },

  content: {
    appName: 'Quizzical AI',
    landingPage: {
      title: 'Discover Your True Personality',
      subtitle: 'A custom personality quiz in seconds for any topic',
      placeholder: 'e.g., Gilmore Girls, Myers Briggs, 90s R&B...',
      buttonText: 'Create My Quiz',
      examples: ['The Office', 'Ancient Rome'],
      inputAriaLabel: 'Quiz Topic',
      validation: {
        minLength: 'Must be at least {min} characters.',
        maxLength: 'Cannot exceed {max} characters.',
      },
    },
    aboutPage: {
      title: 'About Quizzical AI',
      description: 'Learn more about Quizzical AI and how it works.',
    },
    termsPage: {
      title: 'Terms of Service',
      description: 'Read our terms and conditions for using Quizzical AI.',
    },
    privacyPolicyPage: {
      title: 'Privacy Policy',
      description: 'Understand how we handle your data and privacy.',
    },

    // NEW: result page labels (kept optional in types; provided by default here)
    resultPage: {
      titlePrefix: '',
      traitListTitle: 'Your Traits',
      startOverButton: 'Start Another Quiz',
      shareButton: 'Share your result',
      shareCopied: 'Link Copied!',
      // New labels to support primary share and fallback copy UX
      shareText: 'Check out my quiz result!',
      shared: 'Shared!',
      copyLink: 'Copy link instead',
      // optional social metadata block (left blank by default)
      share: {
        socialTitle: '',
        socialDescription: '',
      },
      feedback: {
        prompt: 'What did you think of your result?',
        submit: 'Submit Feedback',
        thanks: 'Thank you for your feedback!',
        thumbsUp: 'Thumbs up',
        thumbsDown: 'Thumbs down',
        commentPlaceholder: 'Add a comment (optional)…',
        turnstileError: 'Please complete the security check before submitting.',
      },
    },

    footer: {
      about:   { label: 'About',   href: '/about'  },
      terms:   { label: 'Terms',   href: '/terms'  },
      privacy: { label: 'Privacy', href: '/privacy'},
      donate:  { label: 'Donate',  href: 'https://github.com/sponsors/your', external: true },
      copyright: 'Quizzical AI',
    },
    loadingStates: { quiz: 'Preparing your quiz...', question: 'Thinking...', page: 'Loading...' },
    errors: {
      title: 'An Error Occurred',
      description: 'Something went wrong. Please try again or return to the home page.',
      requestTimeout: "The request timed out. It's taking longer than expected.",
      quizCreationFailed: 'We were unable to create your quiz at this time.',
      categoryNotFound: "Sorry, we couldn't create a quiz for that category.",
      resultNotFound: 'This result could not be found.',
      startOver: 'Start Over',
      retry: 'Try Again',
      home: 'Go Home',
    },
  },

  limits: { validation: { category_min_length: 3, category_max_length: 80 } },
  apiTimeouts: { default: 15000, startQuiz: 60000, poll: { total: 60000, interval: 1000, maxInterval: 5000 } },
};
