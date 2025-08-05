/**
 * Converts a color string (hex or "R G B") into an RGB triplet.
 * This makes the theme injector resilient to different config formats.
 * @param value - The color string to process.
 * @returns An RGB triplet string like "255 87 51", or null if invalid.
 */
export function toRgbTriplet(value: string): string | null {
  if (!value || typeof value !== 'string') {
    return null;
  }

  // If it's already in "R G B" format, return it directly.
  if (/^\d{1,3} \d{1,3} \d{1,3}$/.test(value)) {
    return value;
  }

  // Handle hex format
  if (value.startsWith('#')) {
    const hex = value.slice(1);
    const fullHex = hex.length === 3 ? hex.split('').map(c => c + c).join('') : hex;

    if (fullHex.length !== 6) {
      return null;
    }

    const r = parseInt(fullHex.slice(0, 2), 16);
    const g = parseInt(fullHex.slice(2, 4), 16);
    const b = parseInt(fullHex.slice(4, 6), 16);

    if (isNaN(r) || isNaN(g) || isNaN(b)) {
      return null;
    }
    return `${r} ${g} ${b}`;
  }

  return null;
}
