/**
 * FE-ERR-PROD-1..5: structured error-code handling in apiService.
 *
 * Targets the `normalizeHttpError` helper exported from apiService.
 */
import { describe, it, expect } from 'vitest';
import { normalizeHttpError } from './apiService';

function mkRes(status: number, headers: Record<string, string> = {}) {
  const h = new Headers(headers);
  return { status, headers: h };
}

describe('normalizeHttpError (FE-ERR-PROD)', () => {
  it('AC-FE-ERR-PROD-1: 429 with Retry-After: 2 -> retriable + retryAfterMs=2000', () => {
    const err = normalizeHttpError(mkRes(429, { 'Retry-After': '2' }), {
      detail: 'too many',
      errorCode: 'RATE_LIMITED',
    });
    expect(err.status).toBe(429);
    expect(err.code).toBe('rate_limited');
    expect(err.errorCode).toBe('RATE_LIMITED');
    expect(err.retriable).toBe(true);
    expect(err.retryAfterMs).toBe(2000);
  });

  it('AC-FE-ERR-PROD-1: 429 without Retry-After -> defaults to 1000ms', () => {
    const err = normalizeHttpError(mkRes(429), {});
    expect(err.retriable).toBe(true);
    expect(err.retryAfterMs).toBe(1000);
  });

  it('AC-FE-ERR-PROD-1: 429 with malformed Retry-After -> defaults to 1000ms', () => {
    const err = normalizeHttpError(mkRes(429, { 'Retry-After': 'soon' }), {});
    expect(err.retryAfterMs).toBe(1000);
  });

  it('AC-FE-ERR-PROD-2: 409 SESSION_BUSY -> code session_busy, not retriable', () => {
    const err = normalizeHttpError(mkRes(409), {
      detail: 'still processing',
      errorCode: 'SESSION_BUSY',
    });
    expect(err.code).toBe('session_busy');
    expect(err.errorCode).toBe('SESSION_BUSY');
    expect(err.retriable).toBe(false);
  });

  it('AC-FE-ERR-PROD-2: 409 without SESSION_BUSY errorCode -> generic http_error', () => {
    const err = normalizeHttpError(mkRes(409), { detail: 'conflict' });
    expect(err.code).not.toBe('session_busy');
  });

  it('AC-FE-ERR-PROD-3: 413 -> overrides message + code payload_too_large', () => {
    const err = normalizeHttpError(mkRes(413), {
      detail: 'big',
      errorCode: 'PAYLOAD_TOO_LARGE',
    });
    expect(err.code).toBe('payload_too_large');
    expect(err.message).toBe('Your input is too long.');
    expect(err.retriable).toBe(false);
  });

  it('AC-FE-ERR-PROD-4: 422 -> code validation_error, preserves detail', () => {
    const err = normalizeHttpError(mkRes(422), {
      detail: 'category contains invalid characters',
    });
    expect(err.code).toBe('validation_error');
    expect(err.message).toBe('category contains invalid characters');
    expect(err.retriable).toBe(false);
  });

  it('AC-FE-ERR-PROD-6: 503 -> code service_unavailable + canonical message, retriable', () => {
    const err = normalizeHttpError(mkRes(503), { detail: 'down' });
    expect(err.code).toBe('service_unavailable');
    expect(err.message).toMatch(/temporarily busy/i);
    expect(err.retriable).toBe(true);
  });

  it('AC-FE-ERR-PROD-6: 504 -> code gateway_timeout + canonical message, retriable', () => {
    const err = normalizeHttpError(mkRes(504), {});
    expect(err.code).toBe('gateway_timeout');
    expect(err.message).toMatch(/timed out/i);
    expect(err.retriable).toBe(true);
  });

  it('AC-FE-ERR-PROD-8: 502 -> code bad_gateway + canonical message, retriable', () => {
    const err = normalizeHttpError(mkRes(502), { detail: 'upstream' });
    expect(err.code).toBe('bad_gateway');
    expect(err.message).toMatch(/upstream/i);
    expect(err.retriable).toBe(true);
  });

  it('§9.7.5 AC-FE-ERR-PROD-1: unenumerated 5xx -> friendly server_error message, retriable, raw detail hidden', () => {
    const err = normalizeHttpError(mkRes(500), { detail: 'NullPointerException at line 42 in db.py' });
    expect(err.code).toBe('server_error');
    expect(err.retriable).toBe(true);
    expect(err.message).not.toMatch(/NullPointer/);
    expect((err.message ?? '').toLowerCase()).toMatch(/something went wrong/);
  });

  it('§9.7.5 AC-FE-ERR-PROD-2: unenumerated 4xx -> friendly client_error message, non-retriable', () => {
    const err = normalizeHttpError(mkRes(400), { detail: 'malformed json at column 12' });
    expect(err.retriable).toBe(false);
    expect(err.code).toBe('client_error');
    expect(err.message).not.toMatch(/malformed/);
    expect((err.message ?? '').toLowerCase()).toMatch(/something went wrong/);
  });

  it('§9.7.5 AC-FE-ERR-PROD-3: friendly mapping does NOT clobber known codes (429 still rate_limited)', () => {
    const err = normalizeHttpError(mkRes(429, { 'Retry-After': '5' }), {});
    expect(err.code).toBe('rate_limited');
    expect(err.errorCode).toBe('RATE_LIMITED');
  });

  it('§9.7.5 AC-FE-ERR-PROD-4: BE-supplied errorCode is preserved on unenumerated 5xx', () => {
    const err = normalizeHttpError(mkRes(500), { errorCode: 'LLM_FAILED', detail: 'internal' });
    expect(err.errorCode).toBe('LLM_FAILED');
    expect((err.message ?? '').toLowerCase()).toMatch(/something went wrong/);
  });

  it('§18 AC-QUALITY-ERR-3: traceId from envelope body is captured on ApiError', () => {
    const err = normalizeHttpError(mkRes(503), {
      detail: 'service down',
      errorCode: 'SERVICE_UNAVAILABLE',
      traceId: 'trace-xyz-123',
    });
    expect(err.traceId).toBe('trace-xyz-123');
    expect(err.errorCode).toBe('SERVICE_UNAVAILABLE');
  });

  it('§18 AC-QUALITY-ERR-3: snake_case trace_id field is also accepted', () => {
    const err = normalizeHttpError(mkRes(409), {
      detail: 'busy',
      errorCode: 'SESSION_BUSY',
      trace_id: 'tr-abc',
    });
    expect(err.traceId).toBe('tr-abc');
  });
});
