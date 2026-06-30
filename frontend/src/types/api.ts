// src/types/api.ts

/**
 * The normalized shape for all API errors handled by the application.
 */
export type ApiError = {
  status?: number;
  code?: string;
  /** Server-provided structured error code (e.g. RATE_LIMITED, SESSION_BUSY, PAYLOAD_TOO_LARGE). */
  errorCode?: string;
  /**
   * Whimsical-error-system (2026-06-30) — the precise internal `QF-...` code the
   * backend assigned to this failure. Shown to the user as light-grey small text
   * below the whimsical message for support triage. FE-only failures (error
   * boundary, config load) use the same `QF-FE-...` scheme via `feErrorCode`.
   */
  qfCode?: string;
  /**
   * Whimsical-error-system (2026-06-30) — the on-brand, user-facing message the
   * backend supplied. Alludes to the cause (never raw technical detail). When
   * present the FE renders this instead of the technical `message`.
   */
  whimsical?: string;
  message?: string;
  retriable?: boolean;
  /** When set (ms), callers should wait at least this long before retrying. Derived from Retry-After. */
  retryAfterMs?: number;
  /** Backend-echoed trace identifier (X-Trace-ID / X-Request-ID), captured for diagnostics. */
  traceId?: string;
  details?: unknown; // For development-only debugging
};