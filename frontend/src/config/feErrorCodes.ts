// frontend/src/config/feErrorCodes.ts
//
// Whimsical-error-system (owner request, 2026-06-30) — FRONTEND-only error codes.
//
// Backend failures already arrive with a precise `QF-...` code + whimsical
// message in the response envelope (see apiService.normalizeHttpError + the
// WhimsicalError component). But some failures are purely client-side and never
// touch the backend:
//   * a React render crash caught by the ErrorBoundary;
//   * the app config failing to load before the first paint;
//   * a network error reaching the API at all.
//
// Those use the SAME `QF-...` scheme (prefix `QF-FE-`) so support triage sees
// one consistent code space, and the SAME WhimsicalError component renders them.
// Keep these tasteful + on-brand, matching the backend voice.

export type FeErrorSpec = {
  /** The `QF-FE-...` code shown as light-grey small text for support triage. */
  code: string;
  /** On-brand, user-facing message. Alludes to the cause, never technical. */
  whimsical: string;
};

export const FE_ERROR_CODES = {
  /** A component threw during render and the ErrorBoundary caught it. */
  RENDER_CRASH: {
    code: 'QF-FE-RENDER-CRASH',
    whimsical:
      "Something hiccuped while painting the page 🎨 — a quick refresh usually sets it right.",
  },
  /** The app config could not be loaded (the backend /config call failed). */
  CONFIG_LOAD: {
    code: 'QF-FE-CONFIG-LOAD',
    whimsical:
      "We couldn't unfurl the welcome mat just yet 🪄 — please refresh in a moment.",
  },
  /** A network request never reached the server (offline / DNS / CORS). */
  NETWORK: {
    code: 'QF-FE-NETWORK',
    whimsical:
      "We couldn't reach our corner of the cloud ☁️ — check your connection and try again.",
  },
  /** Catch-all for any unclassified client-side failure. */
  UNKNOWN: {
    code: 'QF-FE-UNKNOWN',
    whimsical:
      "Something unexpected tangled the threads 🧵 — please try again.",
  },
} as const satisfies Record<string, FeErrorSpec>;

export type FeErrorKey = keyof typeof FE_ERROR_CODES;
