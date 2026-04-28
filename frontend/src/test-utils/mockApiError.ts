// §19.4 AC-QUALITY-R2-FE-ERR-1: canonical helper for constructing realistic
// `ApiError` objects in tests. Use this instead of bare `new Error("oops")`
// so tests exercise the real envelope shape.

import type { ApiError } from '../types/api';

export interface MockApiErrorOptions {
  status?: number;
  code?: string;
  retriable?: boolean;
  retryAfterMs?: number;
  traceId?: string;
  details?: unknown;
  message?: string;
}

/**
 * Build an `ApiError` matching the shape produced by `apiService.normalizeHttpError`.
 *
 * The returned object is a real `Error` (so `instanceof Error` checks pass)
 * with the additional envelope fields attached.
 */
export function mockApiError(errorCode: string, opts: MockApiErrorOptions = {}): ApiError & Error {
  const message = opts.message ?? `API Error: ${errorCode}`;
  const err = new Error(message) as Error & ApiError;
  err.errorCode = errorCode;
  err.code = opts.code ?? errorCode.toLowerCase();
  err.status = opts.status;
  err.retriable = opts.retriable ?? false;
  err.traceId = opts.traceId ?? `trace-${Math.random().toString(36).slice(2, 10)}`;
  if (opts.retryAfterMs !== undefined) err.retryAfterMs = opts.retryAfterMs;
  if (opts.details !== undefined) err.details = opts.details;
  return err;
}
