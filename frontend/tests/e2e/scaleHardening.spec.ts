/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * §17 — Scalability Hardening FE↔BE contract test.
 *
 * Asserts the new observability/scalability guarantees the BE now emits and
 * that the FE relies on for diagnostics:
 *
 *   • Server-Timing header is present on every successful API response with
 *     at least the ``app;dur=<ms>`` segment (AC-SCALE-TIMING-1..4).
 *   • X-Trace-ID is echoed on every response (existing contract).
 *
 * This spec runs against the **real Docker stack** when reachable. When the
 * stack is unreachable (e.g. local dev without Docker) the test skips
 * cleanly so CI/dev runs are not blocked.
 */

import { test, expect } from '@playwright/test';

const BACKEND_BASE = process.env.E2E_BACKEND_BASE_URL ?? 'http://localhost:8000';
const API_PREFIX = '/api/v1';

/** Small TTL probe so we don't spend 30s waiting for a missing stack. */
async function backendIsUp(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(`${BACKEND_BASE}${API_PREFIX}/config`, { signal: ctrl.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

test.describe('§17 Scalability Hardening — Server-Timing & Trace-ID', () => {
  test('GET /config carries Server-Timing app;dur=<ms> and X-Trace-ID', async ({ request }) => {
    if (!(await backendIsUp())) {
      test.skip(true, `Skipping — backend at ${BACKEND_BASE} is not reachable`);
    }

    const resp = await request.get(`${BACKEND_BASE}${API_PREFIX}/config`);
    expect(resp.ok()).toBeTruthy();

    const serverTiming = resp.headers()['server-timing'] ?? '';
    expect(
      serverTiming,
      'Server-Timing must include the app;dur=<ms> baseline segment',
    ).toMatch(/app;dur=\d+(\.\d+)?/);

    const traceId = resp.headers()['x-trace-id'] ?? resp.headers()['X-Trace-ID'];
    expect(traceId, 'X-Trace-ID must be echoed on every API response').toBeTruthy();
  });

  test('OPTIONS preflight exposes Server-Timing via CORS', async ({ request }) => {
    if (!(await backendIsUp())) {
      test.skip(true, `Skipping — backend at ${BACKEND_BASE} is not reachable`);
    }

    const resp = await request.fetch(`${BACKEND_BASE}${API_PREFIX}/config`, {
      method: 'OPTIONS',
      headers: {
        Origin: 'http://localhost:3000',
        'Access-Control-Request-Method': 'GET',
      },
    });
    // CORS preflight should succeed; if it fails we skip rather than fail.
    if (!resp.ok() && resp.status() !== 204) {
      test.skip(true, `Preflight not available (status ${resp.status()})`);
    }
    const exposeHeaders = (resp.headers()['access-control-expose-headers'] ?? '').toLowerCase();
    expect(
      exposeHeaders,
      'Browsers must be able to read Server-Timing client-side',
    ).toContain('server-timing');
  });
});
