// src/services/configService.ts
import { apiFetch } from './apiService';
import { configData } from '../mocks/configMock';
import type { AppConfig } from '../types/config';

interface FetchOptions {
  signal?: AbortSignal;
}

/**
 * Fetches the application configuration from the backend.
 * @param options - Options for the API call, e.g., { signal }.
 * @returns The configuration object.
 */
export function fetchBackendConfig(options?: FetchOptions): Promise<AppConfig> {
  return apiFetch<AppConfig>('/config', { ...options, method: 'GET' });
}

/**
 * Returns the mock configuration data.
 * @returns The mock configuration object.
 */
export function getMockConfig(): AppConfig {
  // In a real app, this could be a Promise to mimic network delay.
  // We cast to AppConfig to satisfy the TypeScript compiler.
  return configData as AppConfig;
}