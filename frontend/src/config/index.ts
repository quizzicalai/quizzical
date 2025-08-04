// src/config/index.ts
import { configData as rawMockConfig } from '../mocks/configMock';
import { validateAndNormalizeConfig } from '../utils/configNormalizer';
import type { AppConfig } from '../types/config';

/**
 * The single, validated source of truth for application configuration.
 */
let appConfig: AppConfig;

try {
  // Validate the mock config on application startup
  appConfig = validateAndNormalizeConfig(rawMockConfig);
} catch (e) {
  console.error("A fatal error occurred during configuration loading:", e);
  alert("Fatal Error: Application configuration is invalid. App may not function correctly. Please check the console.");

  // This fallback now correctly matches the AppConfig type,
  // including the nested 'colors' and 'fonts' objects.
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
  };
}

export { appConfig };
