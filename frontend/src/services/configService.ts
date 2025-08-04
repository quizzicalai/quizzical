// src/services/configService.js
import { apiFetch } from './apiService';
import { configData } from '../mocks/configMock';

/**
 * Fetches the application configuration from the backend.
 * @param {object} options - Options for the API call, e.g., { signal }.
 * @returns {Promise<object>} The configuration object.
 */
export function fetchBackendConfig(options) {
  return apiFetch('/config', { ...options, method: 'GET' });
}

/**
 * Returns the mock configuration data.
 * @returns {object} The mock configuration object.
 */
export function getMockConfig() {
  // In a real app, this could be a Promise to mimic network delay
  return configData;
}