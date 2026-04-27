// AC-FE-OBS-REQID-1/2: every outbound request carries an X-Request-Id and the
// BE-echoed trace id is captured on errors.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiFetch, generateRequestId, initializeApiService } from './apiService';

const REQ_ID_RE = /^[A-Za-z0-9_.\-]{1,128}$/;

describe('generateRequestId', () => {
  it('matches the BE-side X-Request-Id regex', () => {
    for (let i = 0; i < 50; i++) {
      const id = generateRequestId();
      expect(id).toMatch(REQ_ID_RE);
      expect(id.length).toBeGreaterThanOrEqual(1);
      expect(id.length).toBeLessThanOrEqual(128);
    }
  });

  it('returns a different value on each call', () => {
    const a = generateRequestId();
    const b = generateRequestId();
    expect(a).not.toBe(b);
  });
});

describe('apiFetch X-Request-Id propagation', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    initializeApiService({
      apiBaseUrl: 'http://test',
      timeouts: { default: 5000, longRunning: 5000 },
    } as any);
  });

  afterEach(() => {
    fetchSpy?.mockRestore();
  });

  it('AC-FE-OBS-REQID-1: attaches X-Request-Id header to outbound requests', async () => {
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );

    await apiFetch('/health');

    expect(fetchSpy).toHaveBeenCalledOnce();
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const sent = (init.headers as Record<string, string>)['X-Request-Id'];
    expect(sent).toBeDefined();
    expect(sent).toMatch(REQ_ID_RE);
  });

  it('AC-FE-OBS-REQID-2: captures BE-echoed X-Trace-Id on error responses', async () => {
    const traceId = 'srv-trace-12345';
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'boom' }), {
        status: 500,
        headers: {
          'content-type': 'application/json',
          'X-Trace-Id': traceId,
        },
      }),
    );

    await expect(apiFetch('/boom')).rejects.toMatchObject({
      status: 500,
      traceId,
    });
  });

  it('AC-FE-OBS-REQID-2: falls back to X-Request-Id header, then to outbound id, when X-Trace-Id absent', async () => {
    const echo = 'echoed-by-server';
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'boom' }), {
        status: 502,
        headers: {
          'content-type': 'application/json',
          'X-Request-Id': echo,
        },
      }),
    );

    await expect(apiFetch('/boom')).rejects.toMatchObject({
      status: 502,
      code: 'bad_gateway',
      traceId: echo,
    });
  });
});
