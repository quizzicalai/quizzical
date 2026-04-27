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

  it('5xx other than 503/504 remains retriable with generic code', () => {
    const err = normalizeHttpError(mkRes(500), { detail: 'boom' });
    expect(err.code).toBe('http_error');
    expect(err.retriable).toBe(true);
  });

  it('400 stays non-retriable, no special code', () => {
    const err = normalizeHttpError(mkRes(400), { detail: 'bad' });
    expect(err.retriable).toBe(false);
    expect(err.code).toBe('http_error');
  });
});
