// frontend/src/utils/color.spec.ts
import { describe, it, expect } from 'vitest';
import { toRgbTriplet } from './color';

describe('toRgbTriplet', () => {
  it('returns null for falsy or non-string inputs', () => {
    // @ts-expect-error intentional bad inputs for runtime guard
    expect(toRgbTriplet(null)).toBeNull();
    // @ts-expect-error intentional bad inputs for runtime guard
    expect(toRgbTriplet(undefined)).toBeNull();
    // @ts-expect-error intentional bad inputs for runtime guard
    expect(toRgbTriplet(42)).toBeNull();
    // @ts-expect-error intentional bad inputs for runtime guard
    expect(toRgbTriplet({})).toBeNull();
    // empty string
    expect(toRgbTriplet('')).toBeNull();
  });

  it('passes through already formatted "R G B" strings unchanged', () => {
    expect(toRgbTriplet('255 87 51')).toBe('255 87 51');
    // Leading zeros allowed by regex (still just string pass-through)
    expect(toRgbTriplet('007 008 009')).toBe('007 008 009');
  });

  it('converts valid 3-digit hex (#rgb) to "R G B"', () => {
    // #f53 -> ff (255), 55 (85), 33 (51)
    expect(toRgbTriplet('#f53')).toBe('255 85 51');
    // mixed-case also ok (#AbC -> AA (170), BB (187), CC (204))
    expect(toRgbTriplet('#AbC')).toBe('170 187 204');
  });

  it('converts valid 6-digit hex (#rrggbb) to "R G B"', () => {
    expect(toRgbTriplet('#123abc')).toBe('18 58 188');
    expect(toRgbTriplet('#ABCDEF')).toBe('171 205 239');
    expect(toRgbTriplet('#000000')).toBe('0 0 0');
    expect(toRgbTriplet('#ffffff')).toBe('255 255 255');
  });

  it('rejects hex with invalid length', () => {
    expect(toRgbTriplet('#ff')).toBeNull();      // 2 digits -> invalid
    expect(toRgbTriplet('#ffff')).toBeNull();    // 4 digits -> invalid
    expect(toRgbTriplet('#fffff')).toBeNull();   // 5 digits -> invalid
    expect(toRgbTriplet('#fffffff')).toBeNull(); // 7 digits -> invalid
    expect(toRgbTriplet('#abc ')).toBeNull();    // trailing space breaks length
  });

  it('rejects hex with non-hex characters', () => {
    expect(toRgbTriplet('#ggg')).toBeNull();
    expect(toRgbTriplet('#12xz45')).toBeNull();
    expect(toRgbTriplet('#zzzzzz')).toBeNull();
  });

  it('returns null for non-hex, non-"R G B" strings', () => {
    expect(toRgbTriplet('red')).toBeNull();
    expect(toRgbTriplet('255, 0, 0')).toBeNull(); // commas not accepted
    expect(toRgbTriplet(' 255 0 0 ')).toBeNull(); // leading/trailing spaces fail regex
    expect(toRgbTriplet('1000 0 0')).toBeNull();  // >3 digits per segment fails regex
  });
});
