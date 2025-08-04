// src/mocks/configMock.js
export const configData = {
  theme: {
    "primary-color": "#4A90E2",
    "secondary-color": "#50E3C2",
    "accent-color": "#F5A623",
    "neutral-color": "#9B9B9B",
    "background-color": "#FFFFFF",
    "text-color": "#333333",
    "font-family": "'Inter', sans-serif",
  },
  content: {
    appName: "Quizzical",
    landingPage: {
      title: "Unlock Your Inner Persona",
      subtitle: "Answer a few questions and let our AI reveal a surprising profile of you.",
      inputPlaceholder: "e.g., 'Ancient Rome', 'The Marvel Universe', 'Baking'",
      submitButton: "Create My Quiz",
      errorMessages: {
        categoryNotFound: "Sorry, we couldn't find that category. Please try another.",
        creationFailed: "Oops! Something went wrong while creating your quiz. Please try again.",
      },
    },
    quizPage: {
      thinkingMessage: "Thinking...",
      generatingSynopsis: "Crafting your quiz synopsis...",
      generatingQuestion: "Brewing up your next question...",
    },
    resultPage: {
      feedbackPrompt: "Did our AI get it right?",
      feedbackThanks: "Thanks for your feedback!",
      sharePrompt: "Share your result!",
      startOverButton: "Start Another Quiz",
    },
    footer: {
      about: "About",
      copyright: "© 2025 Quizzical AI",
      donate: "Donate",
      terms: "Terms of Use",
      privacy: "Privacy Policy",
    },
  },
  limits: {
    validation: {
      category_min_length: 3,
      category_max_length: 80,
    },
  },
};