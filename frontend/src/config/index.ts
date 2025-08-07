// src/config/index.ts
import { configData as rawMockConfig } from '../mocks/configMock';
import { validateAndNormalizeConfig } from '../utils/configValidation';
import type { AppConfig } from '../types/config';

/**
 * The single, validated source of truth for application configuration.
 */
let appConfig: AppConfig;

try {
  // Validate the mock config on application startup.
  const validatedConfig = validateAndNormalizeConfig(rawMockConfig);

  // Ensure the validated config conforms to the AppConfig type by providing
  // a fallback for the potentially optional 'fonts' property.
  if (!validatedConfig.theme.fonts) {
    validatedConfig.theme.fonts = {};
  }
  
  appConfig = validatedConfig as AppConfig;

} catch (e) {
  console.error("A fatal error occurred during configuration loading:", e);
  alert("Fatal Error: Application configuration is invalid. App may not function correctly. Please check the console.");

  // This fallback now correctly matches the AppConfig type,
  // including all required nested objects like 'apiTimeouts' and 'fonts'.
  appConfig = {
    theme: {
      colors: {},
      fonts: {},
    },
    content: {
      appName: "Quizzical.ai",
      landingPage: {},
      footer: {
        about: { label: "About", href: "/about" },
        terms: { label: "Terms", href: "/terms" },
        privacy: { label: "Privacy", href: "/privacy" },
        donate: { label: "Donate", href: "#" },
      },
      aboutPage: { title: "About", blocks: [] },
      termsPage: { title: "Terms", blocks: [] },
      privacyPolicyPage: { title: "Privacy", blocks: [] },
      errors: {
        title: "Error",
        retry: "Retry",
        home: "Home",
        startOver: "Start Over",
      }
    },
    limits: {
      validation: {
        category_min_length: 3,
        category_max_length: 100,
      },
    },
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
}

export { appConfig };
