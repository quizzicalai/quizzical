// §9.7.2 — AC-FE-IMG-1..5
import { describe, it, expect } from 'vitest';
import { safeImageUrl } from './safeImageUrl';

describe('safeImageUrl (§9.7.2)', () => {
  it('AC-FE-IMG-1: rejects javascript: URI', () => {
    expect(safeImageUrl('javascript:alert(1)')).toBeNull();
  });

  it('AC-FE-IMG-2: passes through allowlisted https URL unchanged', () => {
    const url = 'https://fal.media/files/abc.png';
    expect(safeImageUrl(url)).toBe(url);
  });

  it('AC-FE-IMG-2 (subdomain): passes through subdomain of allowlisted host', () => {
    const url = 'https://v3.fal.media/files/abc.png';
    expect(safeImageUrl(url)).toBe(url);
  });

  it('AC-FE-IMG-3: rejects non-allowlisted host', () => {
    expect(safeImageUrl('https://evil.example.com/x.png')).toBeNull();
  });

  it('allows same-origin relative URLs (/foo.png, ./bar.png, baz.png)', () => {
    expect(safeImageUrl('/foo.png')).toBe('/foo.png');
    expect(safeImageUrl('./bar.png')).toBe('./bar.png');
    expect(safeImageUrl('../baz.png')).toBe('../baz.png');
    expect(safeImageUrl('images/x.png')).toBe('images/x.png');
  });

  it('rejects scheme-relative protocol-skip URLs (//evil/x.png)', () => {
    // `//evil/x.png` would resolve to https://evil/x.png in a browser; the
    // host check rejects unless on the allowlist.
    expect(safeImageUrl('//evil.example.com/x.png')).toBeNull();
  });

  it('AC-FE-IMG-4: returns null for undefined/null/non-string without throwing', () => {
    expect(safeImageUrl(undefined)).toBeNull();
    expect(safeImageUrl(null)).toBeNull();
    expect(safeImageUrl(123)).toBeNull();
    expect(safeImageUrl({})).toBeNull();
  });

  it('rejects http:// even on allowlisted host', () => {
    expect(safeImageUrl('http://fal.media/x.png')).toBeNull();
  });

  it('rejects data: URIs', () => {
    expect(safeImageUrl('data:image/png;base64,xxx')).toBeNull();
  });

  it('rejects empty / whitespace-only input', () => {
    expect(safeImageUrl('')).toBeNull();
    expect(safeImageUrl('   ')).toBeNull();
  });

  it('rejects malformed URLs that look like an absolute scheme', () => {
    // A bare scheme like `ftp:foo` or `vbscript:bad` is rejected because it
    // claims a non-https protocol. Plain relative paths (no colon) are
    // treated as same-origin and allowed by design.
    expect(safeImageUrl('ftp:foo')).toBeNull();
    expect(safeImageUrl('vbscript:msgbox')).toBeNull();
  });

  it('honors a custom allowlist via opts', () => {
    const url = 'https://my.cdn.example/x.png';
    expect(safeImageUrl(url, { allowlist: ['cdn.example'] })).toBe(url);
    expect(safeImageUrl(url, { allowlist: ['fal.media'] })).toBeNull();
  });

  it('empty allowlist disables host check (scheme still enforced)', () => {
    expect(safeImageUrl('https://anything.com/x.png', { allowlist: [] })).toBe(
      'https://anything.com/x.png',
    );
    expect(safeImageUrl('javascript:bad', { allowlist: [] })).toBeNull();
  });
});
