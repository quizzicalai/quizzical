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

  it('renders only chips — no instructional copy, label, or shuffle button', () => {
    render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);

    // No instruction copy, no "Need inspiration?" label, no Shuffle button.
    expect(screen.queryByText(/tap a topic to fill the field instantly/i)).toBeNull();
    expect(screen.queryByText(/need inspiration/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /shuffle/i })).toBeNull();

    // Chips are present and shaped as "Which X am I?".
    const chips = screen.getAllByTestId('topic-suggestion-chip');
    expect(chips.length).toBeGreaterThanOrEqual(12);
    expect(chips[0]).toHaveTextContent(/which/i);
    expect(chips[0]).toHaveTextContent(/am i\?/i);
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
});
