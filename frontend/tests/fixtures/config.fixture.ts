// Shared config used by tests (CT + E2E). Keep aligned with validateAndNormalizeConfig.
export const CONFIG_FIXTURE = {
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
      subtitle: 'Answer a few questions and let our AI reveal a surprising profile of you.',
      placeholder: "e.g., 'Ancient Rome', 'Baking'",
      buttonText: 'Create My Quiz',
      validation: {
        minLength: 'Must be at least {min} characters.',
        maxLength: 'Cannot exceed {max} characters.',
      },
    },
    footer: {
      about:   { label: 'About',       href: '/about'  },
      terms:   { label: 'Terms of Use',href: '/terms'  },
      privacy: { label: 'Privacy',     href: '/privacy'},
      donate:  { label: 'Donate',      href: '#'},
      // copyright optional, safe to include
      copyright: 'Quizzical AI',
    },
    loadingStates: {
      page: 'Loading...',
      quiz: 'Preparing your quiz...',
      question: 'Thinking...',
    },
    errors: {
      title: 'An Error Occurred',
      description: 'Something went wrong. Please try again or return to the home page.',
      requestTimeout: "The request timed out. It's taking longer than expected.",
      quizCreationFailed: 'We were unable to create your quiz at this time.',
      categoryNotFound: "Sorry, we couldn't create a quiz for that category. Please try a different one.",
      resultNotFound: 'This result could not be found.',
      startOver: 'Start Over',
      retry: 'Try Again',
      home: 'Go Home',
    },
    aboutPage:          { title: 'About Quizzical', blocks: [{ type: 'p', text: 'About body' }] },
    termsPage:          { title: 'Terms of Use',    blocks: [{ type: 'p', text: 'Terms body' }] },
    privacyPolicyPage:  { title: 'Privacy Policy',  blocks: [{ type: 'p', text: 'Privacy body' }] },
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
        socialDescription: 'Find out what your favorite topic says about you.',
      },
    },
  },
  limits: {
    validation: { category_min_length: 3, category_max_length: 80 },
  },
  apiTimeouts: {
    default: 15000,
    startQuiz: 60000,
    poll: { total: 60000, interval: 200, maxInterval: 400 },
  },
  // optional in your schema; include for safety
  features: { turnstileEnabled: false },
};
