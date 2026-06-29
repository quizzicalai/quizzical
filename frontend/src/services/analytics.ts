// frontend/src/services/analytics.ts
//
// First-party, vendor-free funnel analytics (P1 Virality §C).
//
// A tiny `track(event, props?)` that POSTs to the backend `/events` endpoint,
// which emits a single structured `analytics.event` log line. There is NO
// third-party SDK, NO cookie, NO persistent identifier, and NO PII — we only
// ever send an allow-listed event name plus a small bag of non-identifying
// scalar props (e.g. `{ method: 'native' }`).
//
// Hard rules enforced here:
//   - Respect Do-Not-Track: if `navigator.doNotTrack === '1'` (or the legacy
//     `window.doNotTrack`/`navigator.msDoNotTrack` variants), we no-op.
//   - Never throw and never block the UI: failures are swallowed. We prefer
//     `navigator.sendBeacon` (survives page unload, fire-and-forget) and fall
//     back to `fetch(..., { keepalive: true })`.
//   - Only the three funnel events are part of the public funnel, but the util
//     itself is event-name agnostic; the backend allow-lists names.

/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

const IS_DEV = import.meta.env.DEV === true;

// Mirror apiService's base-URL resolution so analytics hits the same API origin
// without coupling to apiService's `initializeApiService()` lifecycle (analytics
// may fire independently of the typed API client).
const RAW_API_URL = (import.meta.env.VITE_API_URL as string | undefined) || '';
const RAW_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api/v1';

function stripTrailingSlash(s: string): string {
  return s.endsWith('/') ? s.slice(0, -1) : s;
}

function ensureLeadingSlash(s: string): string {
  if (!s) return '/';
  return s.startsWith('/') ? s : `/${s}`;
}

function isAbsoluteUrl(s: string): boolean {
  return /^https?:\/\//i.test(s);
}

function browserOrigin(): string {
  if (
    typeof window === 'undefined' ||
    !window.location?.origin ||
    window.location.origin === 'null'
  ) {
    return '';
  }
  return stripTrailingSlash(window.location.origin);
}

function resolveEventsUrl(): string {
  let base: string;
  if (RAW_BASE && isAbsoluteUrl(RAW_BASE)) {
    base = stripTrailingSlash(RAW_BASE);
  } else {
    const origin = RAW_API_URL
      ? stripTrailingSlash(RAW_API_URL)
      : IS_DEV
        ? 'http://localhost:8000'
        : browserOrigin();
    const path = ensureLeadingSlash(RAW_BASE || '/api/v1');
    base = `${origin}${stripTrailingSlash(path)}`;
  }
  return `${base}/events`;
}

/**
 * True when the user has expressed a Do-Not-Track preference via any of the
 * historical browser surfaces. SSR-safe (returns false when no globals exist).
 */
export function isDoNotTrackEnabled(): boolean {
  try {
    const nav: any = typeof navigator !== 'undefined' ? navigator : undefined;
    const win: any = typeof window !== 'undefined' ? window : undefined;
    const dnt =
      nav?.doNotTrack ?? win?.doNotTrack ?? nav?.msDoNotTrack ?? undefined;
    // Browsers report '1' / 'yes' when enabled; '0' / 'unspecified' / null when not.
    return dnt === '1' || dnt === 'yes' || dnt === true;
  } catch {
    return false;
  }
}

/** Allow-listed funnel events. Mirrors the backend allow-list. */
export type FunnelEvent = 'quiz_start' | 'quiz_complete' | 'share_click';

/** Small, non-identifying property bag. Values must be plain scalars. */
export type EventProps = Record<string, string | number | boolean>;

/**
 * Strip a props object down to small scalar values only. Defends against
 * accidentally passing objects/PII; the backend enforces the same caps.
 */
function sanitizeProps(props?: EventProps): EventProps | undefined {
  if (!props) return undefined;
  const out: EventProps = {};
  let count = 0;
  for (const [k, v] of Object.entries(props)) {
    if (count >= 10) break;
    if (!k || k.length > 40) continue;
    if (typeof v === 'boolean' || typeof v === 'number') {
      out[k] = v;
      count++;
    } else if (typeof v === 'string') {
      out[k] = v.slice(0, 200);
      count++;
    }
    // silently drop non-scalar values
  }
  return Object.keys(out).length ? out : undefined;
}

/**
 * Fire a funnel event. Fire-and-forget: never returns a rejected promise,
 * never throws, never blocks. No-ops under Do-Not-Track or in non-browser
 * environments.
 */
export function track(event: FunnelEvent, props?: EventProps): void {
  try {
    if (typeof window === 'undefined') return;
    if (isDoNotTrackEnabled()) {
      if (IS_DEV) console.debug('[analytics] suppressed (DNT)', event);
      return;
    }

    const url = resolveEventsUrl();
    const payload: { event: FunnelEvent; props?: EventProps } = { event };
    const cleaned = sanitizeProps(props);
    if (cleaned) payload.props = cleaned;
    const bodyStr = JSON.stringify(payload);

    // Prefer sendBeacon: fire-and-forget, survives navigation/unload, and is
    // exactly the right tool for click/leave funnel events. It only accepts
    // same-origin or CORS-safelisted requests; we send a Blob with an explicit
    // JSON content type.
    const nav: any = navigator;
    if (typeof nav?.sendBeacon === 'function') {
      try {
        const blob = new Blob([bodyStr], { type: 'application/json' });
        const ok = nav.sendBeacon(url, blob);
        if (ok) return;
        // fall through to fetch on failure
      } catch {
        // fall through to fetch
      }
    }

    // Fallback: keepalive fetch so it still completes if the page is unloading.
    if (typeof fetch === 'function') {
      void fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: bodyStr,
        keepalive: true,
        credentials: 'same-origin',
        // Analytics is best-effort; we explicitly ignore the response.
      }).catch(() => {
        /* swallow — analytics must never surface errors */
      });
    }
  } catch (err) {
    // Absolutely never let analytics break the app.
    if (IS_DEV) console.warn('[analytics] track failed (ignored)', err);
  }
}

export default track;
