// frontend/src/services/configService.spec.ts

/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  installFetchMock,
  setEnv,
  loadModule,
  silenceConsole,
} from '../../tests/fixtures/testHarness';
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// NOTE: Use Vite-resolvable root-relative paths (no "frontend/..." prefix)
const API_MOD_PATH = 'src/services/apiService.ts';
const MOD_PATH = 'src/services/configService.ts';

type ApiModule = typeof import('./apiService');
type ConfigModule = typeof import('./configService');

function setupCommonEnv(overrides: Record<string, any> = {}) {
  setEnv({
    VITE_API_URL: 'https://api.test',
    VITE_API_BASE_URL: '/api/v1',
    VITE_USE_DB_RESULTS: 'false',
    VITE_E2E: 'false',
    VITE_USE_MOCK_CONFIG: 'false',
    ...overrides,
  });
}

describe('configService', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetchBackendConfig calls /config over FULL_BASE_URL and returns server config', async () => {
    setupCommonEnv();
    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    fetchMock.mockJsonOnce(200, CONFIG_FIXTURE, { 'content-type': 'application/json' });

    const cfg = await mod.fetchBackendConfig();
    expect(cfg).toEqual(CONFIG_FIXTURE);
    expect(fetchMock.calls[0].url).toBe('https://api.test/api/v1/config');
    expect(fetchMock.calls[0].method).toBe('GET');
  });

  it('getMockConfig returns DEFAULT_APP_CONFIG exactly', async () => {
    setupCommonEnv();
    installFetchMock();
    silenceConsole();

    const { DEFAULT_APP_CONFIG } = await import('../config/defaultAppConfig');
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    const cfg = mod.getMockConfig();
    expect(cfg).toEqual(DEFAULT_APP_CONFIG);
  });

  it('loadAppConfig (DEV, USE_MOCK=true, not E2E) returns mock without hitting the network', async () => {
    setupCommonEnv({ VITE_USE_MOCK_CONFIG: 'true', VITE_E2E: 'false' });
    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const { DEFAULT_APP_CONFIG } = await import('../config/defaultAppConfig');
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    const cfg = await mod.loadAppConfig();
    expect(cfg).toEqual(DEFAULT_APP_CONFIG);
    expect(fetchMock.calls.length).toBe(0);
  });

  it('loadAppConfig (DEV, USE_MOCK=false) returns backend config on success', async () => {
    setupCommonEnv({ VITE_USE_MOCK_CONFIG: 'false' });
    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    fetchMock.mockJsonOnce(200, CONFIG_FIXTURE, { 'content-type': 'application/json' });

    const cfg = await mod.loadAppConfig();
    expect(cfg).toEqual(CONFIG_FIXTURE);
    expect(fetchMock.calls[0].url).toBe('https://api.test/api/v1/config');
  });

  it('loadAppConfig (DEV, USE_MOCK=false) falls back to mock on backend failure (non-cancel)', async () => {
    setupCommonEnv({ VITE_USE_MOCK_CONFIG: 'false' });
    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const { DEFAULT_APP_CONFIG } = await import('../config/defaultAppConfig');
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    fetchMock.mockTextOnce(500, 'server boom', { 'content-type': 'text/plain' });

    const cfg = await mod.loadAppConfig();
    expect(cfg).toEqual(DEFAULT_APP_CONFIG);
  });

  it('loadAppConfig rethrows on cancellation (AbortError / canceled)', async () => {
    setupCommonEnv({ VITE_USE_MOCK_CONFIG: 'false' });
    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    // Case 1: Simulate a native AbortError rejection from fetch.
    // apiService.apiFetch will normalize this to `{ canceled: true, code: 'canceled', ... }`.
    fetchMock.mockRejectOnce({ name: 'AbortError' });
    try {
      await mod.loadAppConfig();
      throw new Error('expected rejection');
    } catch (e: any) {
      // Accept either a native AbortError OR the normalized canceled error.
      const acceptable =
        e?.name === 'AbortError' ||
        e?.canceled === true ||
        e?.code === 'canceled';
      expect(acceptable).toBe(true);
    }

    // Case 2: Simulate the already-normalized canceled error shape.
    fetchMock.mockRejectOnce({ name: 'AbortError' });
        await expect(mod.loadAppConfig()).rejects.toSatisfy((e: any) =>
        e?.name === 'AbortError' || e?.code === 'canceled' || e?.canceled === true
        );
  });

  it('loadAppConfig (E2E=true) always uses HTTP and returns backend config', async () => {
    setupCommonEnv({ VITE_E2E: 'true', VITE_USE_MOCK_CONFIG: 'true' });
    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);

    fetchMock.mockJsonOnce(200, CONFIG_FIXTURE, { 'content-type': 'application/json' });

    const cfg = await mod.loadAppConfig();
    expect(cfg).toEqual(CONFIG_FIXTURE);
    expect(fetchMock.calls.length).toBe(1);
    expect(fetchMock.calls[0].url).toBe('https://api.test/api/v1/config');
  });

  it('loadAppConfig (PROD) does not fall back to mock on backend error — asserts behavior based on baked DEV value', async () => {
    setupCommonEnv({
      VITE_USE_MOCK_CONFIG: 'false',
      VITE_E2E: 'false',
    });
    const DEV_BAKED_TRUE = (import.meta as any).env?.DEV === true;

    const fetchMock = installFetchMock();
    silenceConsole();

    await loadModule<ApiModule>(API_MOD_PATH);
    const mod = await loadModule<typeof import('./configService')>(MOD_PATH);
    const { DEFAULT_APP_CONFIG } = await import('../config/defaultAppConfig');

    fetchMock.mockTextOnce(500, 'server boom', { 'content-type': 'text/plain' });

    if (DEV_BAKED_TRUE) {
      const cfg = await mod.loadAppConfig();
      expect(cfg).toEqual(DEFAULT_APP_CONFIG);
    } else {
      await expect(mod.loadAppConfig()).rejects.toMatchObject({
        status: 500,
        retriable: true,
      });
    }
  });
});
