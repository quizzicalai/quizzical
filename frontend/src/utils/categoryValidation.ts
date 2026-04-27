/**
 * FE-IN-PROD-1..5: client-side category input validation mirroring BE rules.
 *
 * Mirrors backend `validate_category` in app/security/category_validation.py:
 *   - Reject control characters (C0 0..31 except \t, C1 127..159).
 *   - Reject Unicode bidi-override codepoints (U+202A..U+202E, U+2066..U+2069).
 *   - Reject when UTF-8 byte length exceeds 400.
 *   - Whitespace-only is invalid.
 */

const BIDI_CODEPOINTS = new Set([
  0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
  0x2066, 0x2067, 0x2068, 0x2069,
]);

const MAX_BYTE_LEN = 400;

export type CategoryValidation =
  | { ok: true; sanitized: string }
  | { ok: false; reason: 'empty' | 'control_chars' | 'bidi' | 'too_long'; message: string };

/** Internal: collapse runs of whitespace and trim edges. */
function collapseWhitespace(s: string): string {
  return s.replace(/\s+/g, ' ').trim();
}

/** Returns the UTF-8 byte length of a string (uses TextEncoder when available). */
export function utf8ByteLength(s: string): number {
  if (typeof TextEncoder !== 'undefined') {
    return new TextEncoder().encode(s).length;
  }
  // Fallback: blob is too heavy; this manual count is correct for BMP + surrogates.
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff) {
      n += 4;
      i += 1;
    } else n += 3;
  }
  return n;
}

/**
 * Sanitize a category string by normalizing whitespace.
 * Does NOT remove invalid characters — `validateCategory` rejects them instead.
 */
export function sanitizeCategory(input: string): string {
  return collapseWhitespace(input);
}

/**
 * Validate a category string for FE submission.
 *
 * AC-FE-IN-PROD-1..5.
 */
export function validateCategory(input: string): CategoryValidation {
  const sanitized = sanitizeCategory(input);
  if (sanitized.length === 0) {
    return { ok: false, reason: 'empty', message: 'Please enter a topic.' };
  }

  for (let i = 0; i < input.length; i++) {
    const code = input.charCodeAt(i);
    // C0 controls 0..31 except tab (9). C1 controls 127..159.
    if ((code <= 31 && code !== 9) || (code >= 127 && code <= 159)) {
      return {
        ok: false,
        reason: 'control_chars',
        message: 'Topic contains invalid characters.',
      };
    }
    if (BIDI_CODEPOINTS.has(code)) {
      return {
        ok: false,
        reason: 'bidi',
        message: 'Topic contains invalid characters.',
      };
    }
  }

  if (utf8ByteLength(sanitized) > MAX_BYTE_LEN) {
    return { ok: false, reason: 'too_long', message: 'Category is too long.' };
  }

  return { ok: true, sanitized };
}
