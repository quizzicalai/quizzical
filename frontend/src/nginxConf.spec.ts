// Regression test: ensure frontend/nginx.conf ships hardened security headers
// for Docker deployments (FE-SEC-PROD-1..3).
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const NGINX_CONF = readFileSync(
  resolve(__dirname, '..', 'nginx.conf'),
  'utf8',
);

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
    expect(NGINX_CONF).toMatch(/frame-ancestors 'self'/);
    expect(NGINX_CONF).toMatch(/script-src 'self' https:\/\/challenges\.cloudflare\.com/);
  });

  it('FE-SEC-PROD-3: ships a Permissions-Policy disabling sensitive sensors', () => {
    expect(NGINX_CONF).toMatch(/add_header\s+Permissions-Policy\s+"[^"]+"\s+always/);
    expect(NGINX_CONF).toMatch(/camera=\(\)/);
    expect(NGINX_CONF).toMatch(/microphone=\(\)/);
    expect(NGINX_CONF).toMatch(/geolocation=\(\)/);
  });

  it('preserves baseline X-* hardening', () => {
    expect(NGINX_CONF).toMatch(/X-Frame-Options\s+"SAMEORIGIN"/);
    expect(NGINX_CONF).toMatch(/X-Content-Type-Options\s+"nosniff"/);
    expect(NGINX_CONF).toMatch(/Referrer-Policy\s+"strict-origin-when-cross-origin"/);
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
