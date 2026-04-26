// Regression test: ensure staticwebapp.config.json ships hardened security headers.
import { describe, it, expect } from 'vitest';
import config from '../staticwebapp.config.json';

describe('staticwebapp.config.json security headers', () => {
  const headers = (config as any).globalHeaders ?? {};

  it('declares globalHeaders', () => {
    expect(headers).toBeTypeOf('object');
  });

  it('sets X-Frame-Options to DENY', () => {
    expect(headers['X-Frame-Options']).toBe('DENY');
  });

  it('sets X-Content-Type-Options to nosniff', () => {
    expect(headers['X-Content-Type-Options']).toBe('nosniff');
  });

  it('sets a strict Referrer-Policy', () => {
    expect(headers['Referrer-Policy']).toMatch(/strict-origin/);
  });

  it('sets a long-lived HSTS policy', () => {
    expect(headers['Strict-Transport-Security']).toMatch(/max-age=\d{7,}/);
    expect(headers['Strict-Transport-Security']).toMatch(/includeSubDomains/);
  });

  it('sets a Permissions-Policy that disables sensitive features by default', () => {
    expect(headers['Permissions-Policy']).toMatch(/camera=\(\)/);
    expect(headers['Permissions-Policy']).toMatch(/microphone=\(\)/);
    expect(headers['Permissions-Policy']).toMatch(/geolocation=\(\)/);
  });

  it('sets a Content-Security-Policy with safe defaults', () => {
    const csp = headers['Content-Security-Policy'];
    expect(csp).toBeTypeOf('string');
    expect(csp).toMatch(/default-src 'self'/);
    expect(csp).toMatch(/object-src 'none'/);
    expect(csp).toMatch(/frame-ancestors 'none'/);
    expect(csp).toMatch(/base-uri 'self'/);
  });
});
