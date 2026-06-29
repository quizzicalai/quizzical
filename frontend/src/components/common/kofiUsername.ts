/** Extract a Ko-fi username from a ko-fi.com donation URL; null for anything else. */
export function kofiUsername(donationUrl?: string): string | null {
  if (!donationUrl) return null;
  try {
    const u = new URL(donationUrl);
    if (u.protocol !== 'https:') return null;
    if (!/(^|\.)ko-fi\.com$/i.test(u.hostname)) return null;
    const seg = u.pathname.split('/').filter(Boolean)[0];
    return seg ? decodeURIComponent(seg) : null;
  } catch {
    return null;
  }
}
