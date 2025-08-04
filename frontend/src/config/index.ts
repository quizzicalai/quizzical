// src/config/index.ts
import { configData as rawMockConfig } from '../mocks/configMock';
import { validateAndNormalizeConfig } from '../utils/configNormalizer';
import type { AppConfig } from '../types/config';

/**
 * The single, validated source of truth for application configuration.
 * * In a real application, this file would be responsible for:
 * 1. Fetching the configuration from a remote endpoint.
 * 2. Running it through the validator/normalizer.
 * 3. Exporting the final, trusted config object.
 *
 * For development, we use the local mock data.
 */
let appConfig: AppConfig;

try {
  // Validate the mock config on application startup
  appConfig = validateAndNormalizeConfig(rawMockConfig);
} catch (e) {
  console.error(e);
  // In a real app, you might want a hard-coded fallback config here
  // to prevent the entire app from crashing.
  alert("Fatal Error: Application configuration is invalid. Please check the console.");
  // A minimal fallback to prevent total crash
  appConfig = {
      content: { footer: {}, errors: {} },
      theme: { colors: {}, fonts: {} },
      limits: { validation: {} }
  } as unknown as AppConfig;
}

export { appConfig };