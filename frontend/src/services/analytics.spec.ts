// frontend/src/services/analytics.spec.ts
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { setEnv, installFetchMock, loadModule, silenceConsole } from '../../tests/fixtures/testHarness';

// Vite-resolvable root-relative path (matches repo convention).
const MOD_PATH = 'src/services/analytics.ts';
type AnalyticsModule = typeof import('./analytics');

/** Define a (possibly read-only in jsdom) navigator property. */
function setNavProp(prop: string, value: unknown) {
  Object.defineProperty(window.navigator, prop, {
    value,
    configurable: true,
    writable: true,
  });
}

/**
 * Read a Blob's text. jsdom (v27, the repo's test env) does NOT implement
 * `Blob.prototype.text()`, so we use FileReader (which jsdom DOES provide)
 * instead of `await blob.text()`.
 */
function readBlob(b: Blob): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsText(b);
  });
}

const EXPECTED_URL = 'https://api.test/api/v1/events';

describe('analytics.track', () => {
  beforeEach(() => {
    setEnv({ VITE_API_URL: 'https://api.test', VITE_API_BASE_URL: '/api/v1' });
    silenceConsole();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    // Reset DNT + sendBeacon between tests.
    setNavProp('doNotTrack', '0');
    setNavProp('sendBeacon', undefined);
    delete (window as any).doNotTrack;
  });

  it('NO-OPS when Do-Not-Track is enabled (navigator.doNotTrack === "1")', async () => {
    setNavProp('doNotTrack', '1');
    const beacon = vi.fn(() => true);
    setNavProp('sendBeacon', beacon);
    const fetchMock = installFetchMock();

    const mod = await loadModule<AnalyticsModule>(MOD_PATH);
    mod.track('quiz_start');

    expect(beacon).not.toHaveBeenCalled();
    expect(fetchMock.calls.length).toBe(0);
    expect(mod.isDoNotTrackEnabled()).toBe(true);
  });

  it('NO-OPS when DNT is signalled via window.doNotTrack === "yes"', async () => {
    setNavProp('doNotTrack', undefined);
    (window as any).doNotTrack = 'yes';
    const beacon = vi.fn(() => true);
    setNavProp('sendBeacon', beacon);

    const mod = await loadModule<AnalyticsModule>(MOD_PATH);
    mod.track('share_click');

    expect(beacon).not.toHaveBeenCalled();
  });

  it('uses navigator.sendBeacon to POST the event to the /events URL', async () => {
    setNavProp('doNotTrack', '0');
    const beacon = vi.fn(() => true);
    setNavProp('sendBeacon', beacon);

    const mod = await loadModule<AnalyticsModule>(MOD_PATH);
    mod.track('quiz_complete', { method: 'poll' });

    expect(beacon).toHaveBeenCalledTimes(1);
    const [url, blob] = beacon.mock.calls[0];
    expect(url).toBe(EXPECTED_URL);
    // Body is a Blob carrying the JSON payload.
    const text = await readBlob(blob as Blob);
    const parsed = JSON.parse(text);
    expect(parsed.event).toBe('quiz_complete');
    expect(parsed.props).toEqual({ method: 'poll' });
  });

  it('falls back to keepalive fetch when sendBeacon is unavailable', async () => {
    setNavProp('doNotTrack', '0');
    setNavProp('sendBeacon', undefined);
    const fetchMock = installFetchMock();
    fetchMock.mockTextOnce(204, '');

    const mod = await loadModule<AnalyticsModule>(MOD_PATH);
    mod.track('quiz_start');

    expect(fetchMock.calls.length).toBe(1);
    const call = fetchMock.calls[0];
    expect(call.url).toBe(EXPECTED_URL);
    expect(call.method).toBe('POST');
    expect(call.body).toMatchObject({ event: 'quiz_start' });
  });

  it('never throws even if both transports blow up', async () => {
    setNavProp('doNotTrack', '0');
    setNavProp('sendBeacon', () => {
      throw new Error('beacon boom');
    });
    const fetchMock = installFetchMock();
    fetchMock.mockRejectOnce(new Error('network down'));

    const mod = await loadModule<AnalyticsModule>(MOD_PATH);
    expect(() => mod.track('share_click')).not.toThrow();
  });

  it('sanitizes props: drops non-scalar values and caps strings', async () => {
    setNavProp('doNotTrack', '0');
    const beacon = vi.fn(() => true);
    setNavProp('sendBeacon', beacon);

    const mod = await loadModule<AnalyticsModule>(MOD_PATH);
    mod.track('share_click', {
      method: 'x',
      // @ts-expect-error — intentionally passing a disallowed nested value
      nested: { a: 1 },
      big: 'y'.repeat(500),
    } as any);

    const [, blob] = beacon.mock.calls[0];
    const parsed = JSON.parse(await readBlob(blob as Blob));
    expect(parsed.props.method).toBe('x');
    expect(parsed.props.nested).toBeUndefined();
    expect(parsed.props.big.length).toBe(200);
  });
});
