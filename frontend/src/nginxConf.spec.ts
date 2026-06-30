// Regression test: ensure frontend/nginx.conf ships hardened security headers
// for Docker deployments (FE-SEC-PROD-1..3).
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const NGINX_CONF = readFileSync(
  resolve(__dirname, '..', 'nginx.conf'),
  'utf8',
);

// Live SWA config — the source of truth the nginx CSP must mirror (Hitlist #13).
const SWA_CONFIG = JSON.parse(
  readFileSync(resolve(__dirname, '..', 'staticwebapp.config.json'), 'utf8'),
) as { globalHeaders: Record<string, string> };

/** Extract the CSP string from the nginx `add_header Content-Security-Policy "..."`. */
function nginxCsp(): string {
  const m = NGINX_CONF.match(
    /add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always/,
  );
  if (!m) throw new Error('nginx.conf has no Content-Security-Policy header');
  return m[1];
}

/** Parse a CSP string into a directive -> sorted-sources map for order-insensitive comparison. */
function parseCsp(csp: string): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const part of csp.split(';')) {
    const tokens = part.trim().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) continue;
    const [name, ...sources] = tokens;
    out[name] = sources.sort();
  }
  return out;
}

describe('frontend/nginx.conf security headers (FE-SEC-PROD)', () => {
  it('FE-SEC-PROD-1: ships HSTS with includeSubDomains', () => {
    expect(NGINX_CONF).toMatch(
      /add_header\s+Strict-Transport-Security\s+"max-age=\d{7,};\s*includeSubDomains"\s+always/,
    );
  });

  it('FE-SEC-PROD-2: ships a Content-Security-Policy with safe defaults', () => {
    expect(NGINX_CONF).toMatch(/add_header\s+Content-Security-Policy\s+"[^"]+"\s+always/);
    expect(NGINX_CONF).toMatch(/default-src 'self'/);
    expect(NGINX_CONF).toMatch(/object-src 'none'/);
    expect(NGINX_CONF).toMatch(/base-uri 'self'/);
    // Hitlist #13: tightened from 'self' -> 'none' (deny ALL framing) to match SWA.
    expect(NGINX_CONF).toMatch(/frame-ancestors 'none'/);
    expect(NGINX_CONF).toMatch(/script-src 'self' https:\/\/challenges\.cloudflare\.com/);
    // Hitlist #13: COOP + upgrade-insecure-requests added; X-XSS-Protection dropped.
    expect(NGINX_CONF).toMatch(/upgrade-insecure-requests/);
    expect(NGINX_CONF).toMatch(
      /add_header\s+Cross-Origin-Opener-Policy\s+"same-origin"\s+always/,
    );
    // Deprecated header must not be emitted as a response header (comments OK).
    expect(NGINX_CONF).not.toMatch(/add_header\s+X-XSS-Protection/);
  });

  // FE-SEC-PROD-2b (Hitlist #13): connect-src must be an EXPLICIT allowlist —
  // never a blanket `https:` (which would let XHR/fetch/beacon exfiltrate to any
  // https origin). It must scope to the backend Container App + Turnstile + Ko-fi.
  it('FE-SEC-PROD-2b: connect-src is an explicit allowlist, not blanket https:', () => {
    const connectSrc = nginxCsp().match(/connect-src[^;]*/)?.[0] ?? '';
    expect(connectSrc).toBeTruthy();
    // No bare `https:` token (would be a wildcard). `https://host` is fine.
    expect(connectSrc).not.toMatch(/\bhttps:(?!\/\/)/);
    expect(connectSrc).toMatch(/'self'/);
    expect(connectSrc).toMatch(/https:\/\/\*\.azurecontainerapps\.io/);
    expect(connectSrc).toMatch(/https:\/\/challenges\.cloudflare\.com/);
    expect(connectSrc).toMatch(/https:\/\/ko-fi\.com/);
    expect(connectSrc).toMatch(/https:\/\/storage\.ko-fi\.com/);
  });

  it('FE-SEC-PROD-3: ships a Permissions-Policy disabling sensitive sensors', () => {
    expect(NGINX_CONF).toMatch(/add_header\s+Permissions-Policy\s+"[^"]+"\s+always/);
    expect(NGINX_CONF).toMatch(/camera=\(\)/);
    expect(NGINX_CONF).toMatch(/microphone=\(\)/);
    expect(NGINX_CONF).toMatch(/geolocation=\(\)/);
  });

  it('preserves baseline X-* hardening (X-Frame-Options DENY mirrors SWA)', () => {
    // Hitlist #13: tightened SAMEORIGIN -> DENY to match the SWA X-Frame-Options.
    expect(NGINX_CONF).toMatch(/X-Frame-Options\s+"DENY"/);
    expect(NGINX_CONF).toMatch(/X-Content-Type-Options\s+"nosniff"/);
    expect(NGINX_CONF).toMatch(/Referrer-Policy\s+"strict-origin-when-cross-origin"/);
  });

  // FE-SEC-PROD-6 (Hitlist #13): the nginx CSP and the SWA CSP are two
  // deploy-target copies of ONE policy and MUST stay in lockstep. This compares
  // them directive-by-directive (order-insensitive) so any future edit to one
  // file that is not mirrored in the other fails CI loudly.
  it('FE-SEC-PROD-6: nginx CSP is in lockstep with the SWA CSP', () => {
    const swaCsp = SWA_CONFIG.globalHeaders['Content-Security-Policy'];
    expect(swaCsp, 'staticwebapp.config.json must define a CSP').toBeTruthy();
    expect(parseCsp(nginxCsp())).toEqual(parseCsp(swaCsp));
  });

  // FE-SEC-PROD-4: CSP must allow the runtime resources actually used by the
  // SPA — fal.media images, Cloudflare Turnstile (script + iframe), and
  // Google Fonts (style + woff2). Catches accidental tightening that would
  // break image rendering or the captcha widget in production.
  it('FE-SEC-PROD-4: CSP allows FAL images, Turnstile and Google Fonts', () => {
    // img-src must include https: (covers v2.fal.media/files/...)
    expect(NGINX_CONF).toMatch(/img-src[^;]*https:/);
    // script-src + frame-src must allow Cloudflare Turnstile.
    expect(NGINX_CONF).toMatch(
      /script-src[^;]*https:\/\/challenges\.cloudflare\.com/,
    );
    expect(NGINX_CONF).toMatch(
      /frame-src[^;]*https:\/\/challenges\.cloudflare\.com/,
    );
    // Google Fonts: style + font.
    expect(NGINX_CONF).toMatch(/style-src[^;]*https:\/\/fonts\.googleapis\.com/);
    expect(NGINX_CONF).toMatch(/font-src[^;]*https:\/\/fonts\.gstatic\.com/);
  });

  // Ko-fi donate widget: overlay script (storage.ko-fi.com) + overlay iframe
  // (ko-fi.com). Guards the CSP allowances the floating Donate button needs.
  it('FE-SEC-PROD-4b: CSP allows the Ko-fi donate widget', () => {
    expect(NGINX_CONF).toMatch(/script-src[^;]*https:\/\/storage\.ko-fi\.com/);
    expect(NGINX_CONF).toMatch(/frame-src[^;]*https:\/\/ko-fi\.com/);
  });

  // FE-SEC-PROD-5: script-src must NOT include unsafe-inline or unsafe-eval.
  // Tailwind injects style tags (allowed by 'unsafe-inline' on style-src),
  // but we never want inline scripts or eval.
  it('FE-SEC-PROD-5: CSP forbids unsafe-eval and inline scripts', () => {
    const cspMatch = NGINX_CONF.match(
      /add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always/,
    );
    expect(cspMatch).toBeTruthy();
    const csp = cspMatch![1];
    const scriptSrc = csp.match(/script-src[^;]+/)?.[0] ?? '';
    expect(scriptSrc).not.toMatch(/'unsafe-eval'/);
    expect(scriptSrc).not.toMatch(/'unsafe-inline'/);
  });
});
