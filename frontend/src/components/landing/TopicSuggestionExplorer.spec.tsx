import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import TopicSuggestionExplorer, { TOPIC_POOL_SIZE } from './TopicSuggestionExplorer';

afterEach(() => {
  cleanup();
});

describe('TopicSuggestionExplorer', () => {
  it('builds suggestions from a pool of several thousand topics', () => {
    expect(TOPIC_POOL_SIZE).toBeGreaterThanOrEqual(2000);
  });

  it('does not surface synthetic guide/prompt prefixes in visible suggestions', () => {
    render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);
    const chips = screen.getAllByTestId('topic-suggestion-chip');
    // Only block prefixes that read as articles/guides, not as quiz prompts.
    // Aesthetic descriptors (modern/vintage/classic/etc.) are intentional and
    // read naturally inside "Which modern bedroom aesthetic am I?".
    const BAD_PREFIX = /^(fundamentals of|principles of|beginners guide to|comprehensive guide to|how |why )/i;
    for (const chip of chips) {
      const text = chip.textContent ?? '';
      const noun = text.replace(/^\s*Which\s*/i, '').replace(/\s*am I\?\s*$/i, '').trim();
      expect(noun).not.toMatch(BAD_PREFIX);
    }
  });

  it('renders chips plus a labeled "Load more" shuffle affordance (no instructional label)', () => {
    render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);

    // No instructional copy — the shuffle affordance is a labeled
    // "Load more" button (icon + text).
    expect(screen.queryByText(/tap a topic to fill the field instantly/i)).toBeNull();
    expect(screen.queryByText(/need inspiration/i)).toBeNull();

    // Chips are present and shaped as "Which X am I?".
    const chips = screen.getAllByTestId('topic-suggestion-chip');
    expect(chips.length).toBeGreaterThanOrEqual(12);
    expect(chips[0]).toHaveTextContent(/which/i);
    expect(chips[0]).toHaveTextContent(/am i\?/i);

    // Shuffle button now reads "Load more" with the shuffle icon.
    const shuffle = screen.getByRole('button', { name: /load more suggestions/i });
    expect(shuffle).toHaveAttribute('title', 'Load more suggestions');
    expect(shuffle.textContent ?? '').toMatch(/load more/i);
  });

  it('clicking shuffle re-renders the suggestion list', () => {
    // Make randomness deterministic but distinct per call so re-shuffles
    // produce a different order.
    const sequence = [0.1, 0.9, 0.4, 0.7, 0.2, 0.6, 0.3, 0.8, 0.5, 0.05];
    let i = 0;
    const spy = vi.spyOn(Math, 'random').mockImplementation(() => {
      const v = sequence[i % sequence.length];
      i += 1;
      return v;
    });
    try {
      render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);
      const before = screen
        .getAllByTestId('topic-suggestion-chip')
        .map((c) => c.textContent);

      const shuffle = screen.getByRole('button', { name: /load more suggestions/i });
      fireEvent.click(shuffle);

      const after = screen
        .getAllByTestId('topic-suggestion-chip')
        .map((c) => c.textContent);

      // Same number of chips, but at least one position differs.
      expect(after).toHaveLength(before.length);
      expect(after.some((txt, idx) => txt !== before[idx])).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });

  it('selecting a chip calls onSelectTopic with the bare noun phrase (not the full question)', () => {
    const onSelectTopic = vi.fn();
    render(<TopicSuggestionExplorer onSelectTopic={onSelectTopic} />);

    const firstChip = screen.getAllByTestId('topic-suggestion-chip')[0];
    fireEvent.click(firstChip);
    expect(onSelectTopic).toHaveBeenCalledTimes(1);
    const value = onSelectTopic.mock.calls[0][0] as string;
    expect(typeof value).toBe('string');
    expect(value.toLowerCase()).not.toMatch(/^which\b/);
    expect(value.toLowerCase()).not.toMatch(/\bam i\?$/);
  });

  it('exposes the chip cloud as a labeled region for assistive tech', () => {
    render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);
    expect(screen.getByRole('region', { name: /suggested quiz topics/i })).toBeInTheDocument();
  });

  it('renders a "Popular" subsection with exactly three chips above a "Random" subsection', () => {
    render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);

    // Both subgroups must be present and individually labeled for AT.
    const popular = screen.getByRole('group', { name: /popular quiz topics/i });
    const random = screen.getByRole('group', { name: /random quiz topics/i });
    expect(popular).toBeInTheDocument();
    expect(random).toBeInTheDocument();

    // Popular is always exactly 3 chips.
    const popularChips = popular.querySelectorAll('[data-testid="topic-suggestion-chip"]');
    expect(popularChips).toHaveLength(3);

    // Random has the wider cloud (>= 12 chips). The two together share the
    // single `topic-suggestion-chip` testid.
    const randomChips = random.querySelectorAll('[data-testid="topic-suggestion-chip"]');
    expect(randomChips.length).toBeGreaterThanOrEqual(12);

    // Visual order: Popular block must come before Random in DOM order.
    const region = screen.getByRole('region', { name: /suggested quiz topics/i });
    const order = Array.from(region.children).map((el) => el.getAttribute('data-testid'));
    const popularIdx = order.indexOf('topic-suggestion-popular');
    const randomIdx = order.indexOf('topic-suggestion-random');
    expect(popularIdx).toBeGreaterThanOrEqual(0);
    expect(randomIdx).toBeGreaterThan(popularIdx);
  });

  it('clicking "Load more" reshuffles BOTH the popular and random subsections', () => {
    // Deterministic randomness across many calls so we can detect that both
    // groups regenerate.
    const sequence = [0.11, 0.83, 0.42, 0.77, 0.24, 0.61, 0.36, 0.88, 0.55, 0.04, 0.71, 0.19];
    let i = 0;
    const spy = vi.spyOn(Math, 'random').mockImplementation(() => {
      const v = sequence[i % sequence.length];
      i += 1;
      return v;
    });
    try {
      render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);

      const popularBefore = Array.from(
        screen
          .getByRole('group', { name: /popular quiz topics/i })
          .querySelectorAll('[data-testid="topic-suggestion-chip"]'),
      ).map((el) => el.textContent);
      const randomBefore = Array.from(
        screen
          .getByRole('group', { name: /random quiz topics/i })
          .querySelectorAll('[data-testid="topic-suggestion-chip"]'),
      ).map((el) => el.textContent);

      fireEvent.click(screen.getByRole('button', { name: /load more suggestions/i }));

      const popularAfter = Array.from(
        screen
          .getByRole('group', { name: /popular quiz topics/i })
          .querySelectorAll('[data-testid="topic-suggestion-chip"]'),
      ).map((el) => el.textContent);
      const randomAfter = Array.from(
        screen
          .getByRole('group', { name: /random quiz topics/i })
          .querySelectorAll('[data-testid="topic-suggestion-chip"]'),
      ).map((el) => el.textContent);

      expect(popularAfter).toHaveLength(popularBefore.length);
      expect(randomAfter).toHaveLength(randomBefore.length);

      // BOTH groups must show some change after a single shuffle click.
      expect(popularAfter.some((txt, idx) => txt !== popularBefore[idx])).toBe(true);
      expect(randomAfter.some((txt, idx) => txt !== randomBefore[idx])).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });
});
