// src/services/configService.ts
import { apiFetch } from './apiService'
import { DEFAULT_APP_CONFIG } from '../config/defaultAppConfig'
import type { AppConfig } from '../types/config'

interface FetchOptions {
  signal?: AbortSignal
  timeoutMs?: number
}

/** Fetches the application configuration from the backend. */
export function fetchBackendConfig(options?: FetchOptions): Promise<AppConfig> {
  return apiFetch<AppConfig>('/config', { ...options, method: 'GET' })
}

/**
 * Returns a small, runtime default config used when
 * VITE_USE_MOCK_CONFIG === 'true' for local development.
 * (Not used by tests — tests should stub HTTP.)
 */
export function getMockConfig(): AppConfig {
  return DEFAULT_APP_CONFIG
}
