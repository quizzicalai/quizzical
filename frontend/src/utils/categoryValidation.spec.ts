import { describe, it, expect } from 'vitest';
import { validateCategory, sanitizeCategory, utf8ByteLength } from './categoryValidation';

describe('categoryValidation (FE-IN-PROD)', () => {
  it('AC-FE-IN-PROD-1: rejects C0 control chars (NUL)', () => {
    const r = validateCategory('hello\u0000world');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('control_chars');
  });

  it('AC-FE-IN-PROD-1: rejects DEL (0x7F)', () => {
    const r = validateCategory('hello\u007fworld');
    expect(r.ok).toBe(false);
  });

  it('AC-FE-IN-PROD-1: tab is allowed (treated as whitespace)', () => {
    const r = validateCategory('hello\tworld');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.sanitized).toBe('hello world');
  });

  it('AC-FE-IN-PROD-2: rejects bidi LRO U+202D', () => {
    const r = validateCategory('hello\u202devil');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('bidi');
  });

  it('AC-FE-IN-PROD-2: rejects bidi RLI U+2067', () => {
    const r = validateCategory('a\u2067b');
    expect(r.ok).toBe(false);
  });

  it('AC-FE-IN-PROD-3: rejects > 400 UTF-8 byte length', () => {
    const r = validateCategory('a'.repeat(401));
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBe('too_long');
      expect(r.message).toBe('Category is too long.');
    }
  });

  it('AC-FE-IN-PROD-3: 4-byte glyph (\u{1F600}) counts as 4 bytes', () => {
    expect(utf8ByteLength('\u{1F600}')).toBe(4);
    // 100 * 4 = 400 -> ok; 101 * 4 = 404 -> too long
    expect(validateCategory('\u{1F600}'.repeat(100)).ok).toBe(true);
    expect(validateCategory('\u{1F600}'.repeat(101)).ok).toBe(false);
  });

  it('AC-FE-IN-PROD-4: whitespace-only is invalid', () => {
    const r = validateCategory('   \t  ');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('empty');
  });

  it('AC-FE-IN-PROD-5: sanitizeCategory collapses internal whitespace', () => {
    expect(sanitizeCategory('  foo   bar  ')).toBe('foo bar');
  });

  it('happy path: returns sanitized string', () => {
    const r = validateCategory('  Quantum   Physics  ');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.sanitized).toBe('Quantum Physics');
  });
});
