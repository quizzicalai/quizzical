/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

// IMPORTANT: use the SAME specifier the tests use:
// Update the import path if the file is located elsewhere, e.g.:
import * as api from '../services/apiService';
// Or create the file '../apiService.ts' if it does not exist.
import { DEFAULT_APP_CONFIG } from '../config/defaultAppConfig';

interface FetchOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

const IS_DEV = import.meta.env.DEV === true;
const IS_E2E = (import.meta.env.VITE_E2E ?? 'false') === 'true';
const USE_MOCK = (import.meta.env.VITE_USE_MOCK_CONFIG ?? 'false') === 'true';

/** Fetches the application configuration from the backend (raw/partial). */
export function fetchBackendConfig(options?: FetchOptions): Promise<unknown> {
  // Calling the property on the module object makes vi.spyOn(api, 'apiFetch') work.
  return api.apiFetch<unknown>('/config', { ...options, method: 'GET' });
}

/** Returns the local default app config (single source of defaults). */
export function getMockConfig(): unknown {
  return DEFAULT_APP_CONFIG as unknown;
}

/**
 * Load config with the right source:
 * - E2E: always via HTTP (Playwright will stub it).
 * - Dev: if VITE_USE_MOCK_CONFIG=true -> mock; else try HTTP and fall back to mock on failure.
 * - Prod: always HTTP.
 *
 * Returns the *raw* config. Validation/merging is done in ConfigContext.
 */
export async function loadAppConfig(options?: FetchOptions): Promise<unknown> {
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
    // Ignore benign cancellations (effect unmount / StrictMode)
    if (e?.canceled === true || e?.name === 'AbortError') {
      if (IS_DEV) console.debug('[config] backend fetch canceled; rethrowing');
      throw e;
    }

    if (IS_DEV) {
      console.warn('[config] backend failed in dev; falling back to mock', e);
      return getMockConfig();
    }

    throw e;
  }
}
