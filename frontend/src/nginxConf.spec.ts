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
});
