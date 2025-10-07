/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import { apiFetch } from './apiService';
import { DEFAULT_APP_CONFIG } from '../config/defaultAppConfig';
import type { AppConfig } from '../utils/configValidation';

interface FetchOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

const IS_DEV = import.meta.env.DEV === true;
const IS_E2E = (import.meta.env.VITE_E2E ?? 'false') === 'true';
const USE_MOCK = (import.meta.env.VITE_USE_MOCK_CONFIG ?? 'false') === 'true';

/** Fetches the application configuration from the backend. */
export function fetchBackendConfig(options?: FetchOptions): Promise<AppConfig> {
  return apiFetch<AppConfig>('/config', { ...options, method: 'GET' });
}

/** Returns a small, runtime default config for local development. */
export function getMockConfig(): AppConfig {
  return DEFAULT_APP_CONFIG;
}

/**
 * Load config with the right source:
 * - E2E: always via HTTP (Playwright will stub it).
 * - Dev: if VITE_USE_MOCK_CONFIG=true -> mock; else try HTTP and fall back to mock on failure.
 * - Prod: always HTTP.
 */
export async function loadAppConfig(options?: FetchOptions): Promise<AppConfig> {
  const preferMock = !IS_E2E && USE_MOCK && IS_DEV;

  if (preferMock) {
    if (IS_DEV) console.debug('[config] using local mock (VITE_USE_MOCK_CONFIG)');
    return getMockConfig();
  }

  try {
    const cfg = await fetchBackendConfig(options);
    if (IS_DEV) console.debug('[config] loaded from backend', cfg);
    return cfg;
  } catch (e: any) {
    // Ignore benign cancelations (effect unmount / StrictMode)
    if (e?.canceled === true || e?.name === 'AbortError') {
      if (IS_DEV) console.debug('[config] backend fetch canceled; ignoring');
      throw e;
    }

    if (IS_DEV) {
      console.warn('[config] backend failed in dev; falling back to mock', e);
      return getMockConfig();
    }

    throw e;
  }
}
