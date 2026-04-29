import { describe, it, expect } from 'vitest';
import { getPlaceholderTopicPool } from '../../data/placeholderTopics';

describe('placeholderTopics pool', () => {
  it('exposes a deduplicated pool with at least 1,000 entries', () => {
    const pool = getPlaceholderTopicPool();
    expect(pool.length).toBeGreaterThanOrEqual(1000);
    const lower = pool.map((s) => s.toLowerCase());
    const unique = new Set(lower);
    expect(unique.size).toBe(pool.length);
  });

  it('contains expected curated personality-quiz prompts', () => {
    const pool = getPlaceholderTopicPool();
    const lower = pool.map((s) => s.toLowerCase());
    expect(lower).toContain('hogwarts house');
    expect(lower).toContain('greek god');
    expect(lower).toContain('myers-briggs type');
  });

  it('rejects synthetic catalog prefixes that read awkwardly in the question frame', () => {
    const pool = getPlaceholderTopicPool();
    const REJECTED = ['Exploring ', 'History of ', 'Future of ', 'Why ',
      'Beginners Guide to ', 'Comprehensive Guide to ', 'Mastering ',
      'Understanding ', 'Evolution of ', 'Principles of ', 'Innovations in '];
    for (const t of pool) {
      for (const p of REJECTED) {
        expect(t.startsWith(p), `"${t}" should not start with "${p}"`).toBe(false);
      }
    }
  });

  it('memoises the pool across calls (referentially equal)', () => {
    const a = getPlaceholderTopicPool();
    const b = getPlaceholderTopicPool();
    expect(a).toBe(b);
  });

  it('uses standardized capitalization (no entry begins with a lowercase letter)', () => {
    const pool = getPlaceholderTopicPool();
    for (const t of pool) {
      const first = t.charAt(0);
      // Letters must be uppercase; non-letter starts (digits, &, etc.) are fine.
      const lower = first.toLocaleLowerCase();
      const upper = first.toLocaleUpperCase();
      const isLetter = lower !== upper;
      if (isLetter) {
        expect(first === upper, `"${t}" must start with an uppercase letter`).toBe(true);
      }
    }
  });
});
