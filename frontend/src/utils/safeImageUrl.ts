/**
 * §9.7.2 — Safe image URL helper (AC-FE-IMG-1..5).
 *
 * Validates URLs before they are bound to `<img src>` for LLM/FAL-derived
 * images. The defence-in-depth mirror of the backend allowlist in
 * `app/services/image_service.py::_validate_image_url`.
 *
 * Returns the URL when:
 *  - it parses as a URL; and
 *  - the protocol is exactly `https:`; and
 *  - the host matches the allowlist (exact host or `*.host` suffix).
 *
 * Otherwise returns `null`. Callers should omit the `<img>` entirely when
 * `null` is returned (no broken icon, no placeholder text leaking the URL).
 *
 * The default allowlist matches the backend (`fal.media`). Tests/dev can
 * extend via `VITE_IMAGE_URL_ALLOWLIST` (comma-separated). An empty list
 * disables the host check (scheme check still applies).
 */

// AC-UX-2026-05-02 — extend the default allowlist beyond just `fal.media`
// so the share-card preview image can load when the result image has been
// migrated to Azure Blob (the production storage backend) or served via
// the Static Web App's own host. Without these entries, `safeImageUrl`
// returned null and the share modal's preview thumbnail silently dropped
// out — leaving the preview card empty and the modal panel appearing
// "transparent". The backend host allowlist mirrors this set.
const DEFAULT_ALLOWLIST = [
  'fal.media',
  'blob.core.windows.net',
  'azureedge.net',
  'azurefd.net',
  'azurewebsites.net',
  'azurestaticapps.net',
  // feat/answer-images-ship (2026-07-02) — pre-computed answer-option images
  // are rehosted into media_assets and served from the API's own
  // `GET /api/v1/media/{id}` endpoint on the Container Apps host. Without this
  // entry safeImageUrl returned null for those durable URLs and the AnswerGrid
  // all-or-none gate hid every answer image.
  'azurecontainerapps.io',
];

function readEnvAllowlist(): string[] | null {
  try {
    const raw = (import.meta as any)?.env?.VITE_IMAGE_URL_ALLOWLIST;
    if (typeof raw !== 'string') return null;
    const parts = raw
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
    return parts;
  } catch {
    return null;
  }
}

export interface SafeImageUrlOptions {
  /** Override the allowlist for this call (e.g. for tests). */
  allowlist?: string[];
}

function hostAllowed(host: string, allowlist: string[]): boolean {
  if (allowlist.length === 0) return true;
  const h = host.toLowerCase();
  for (const allowed of allowlist) {
    if (h === allowed || h.endsWith('.' + allowed)) return true;
  }
  return false;
}

export function safeImageUrl(
  url: unknown,
  opts?: SafeImageUrlOptions,
): string | null {
  if (typeof url !== 'string') return null;
  const trimmed = url.trim();
  if (!trimmed) return null;
  // Scheme-relative URLs (`//host/path`) inherit the page's https scheme but
  // can point at any host — apply the full host check by parsing.
  if (trimmed.startsWith('//')) {
    let parsed: URL;
    try {
      parsed = new URL('https:' + trimmed);
    } catch {
      return null;
    }
    if (!parsed.hostname) return null;
    const allowlist =
      opts?.allowlist ?? readEnvAllowlist() ?? DEFAULT_ALLOWLIST;
    if (!hostAllowed(parsed.hostname, allowlist)) return null;
    return trimmed;
  }
  // Same-origin relative URLs (`/foo.png`, `./foo.png`, `foo.png`) are
  // inherently safe — they cannot encode a `javascript:` payload because
  // they have no scheme. Allow them through.
  if (
    trimmed.startsWith('/') ||
    trimmed.startsWith('./') ||
    trimmed.startsWith('../')
  ) {
    return trimmed;
  }
  // A scheme-relative URL like `//evil.example/x.png` resolves to https in
  // browsers — apply the full check by parsing against a synthetic base.
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    // Plain relative path with no leading slash and no protocol
    // (`foo.png`, `images/x.png`) — treat as safe.
    if (!/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) {
      return trimmed;
    }
    return null;
  }
  if (parsed.protocol !== 'https:') return null;
  if (!parsed.hostname) return null;
  const allowlist =
    opts?.allowlist ?? readEnvAllowlist() ?? DEFAULT_ALLOWLIST;
  if (!hostAllowed(parsed.hostname, allowlist)) return null;
  return trimmed;
}

export default safeImageUrl;
