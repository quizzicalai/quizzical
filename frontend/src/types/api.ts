// src/types/api.ts

/**
 * The normalized shape for all API errors handled by the application.
 */
export type ApiError = {
  status?: number;
  code?: string;
  /** Server-provided structured error code (e.g. RATE_LIMITED, SESSION_BUSY, PAYLOAD_TOO_LARGE). */
  errorCode?: string;
  message?: string;
  retriable?: boolean;
  /** When set (ms), callers should wait at least this long before retrying. Derived from Retry-After. */
  retryAfterMs?: number;
  /** Backend-echoed trace identifier (X-Trace-ID / X-Request-ID), captured for diagnostics. */
  traceId?: string;
  details?: unknown; // For development-only debugging
};