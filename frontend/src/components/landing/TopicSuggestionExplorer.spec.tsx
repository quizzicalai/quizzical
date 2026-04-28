import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import TopicSuggestionExplorer from './TopicSuggestionExplorer';

afterEach(() => {
  cleanup();
});

describe('TopicSuggestionExplorer', () => {
  it('renders a heading and helper copy for suggestion context', () => {
    render(<TopicSuggestionExplorer onSelectTopic={() => {}} />);

    expect(screen.getByRole('heading', { name: /need inspiration/i })).toBeInTheDocument();
    expect(screen.getByText(/tap a topic to fill the field instantly/i)).toBeInTheDocument();
  });

  it('renders a labeled shuffle action and clickable topic chips', () => {
    const onSelectTopic = vi.fn();
    render(<TopicSuggestionExplorer onSelectTopic={onSelectTopic} />);

    const shuffle = screen.getAllByRole('button', { name: /shuffle ideas/i })[0];
    expect(shuffle).toBeInTheDocument();

    const firstChip = screen.getAllByTestId('topic-suggestion-chip')[0];
    fireEvent.click(firstChip);
    expect(onSelectTopic).toHaveBeenCalledTimes(1);
  });
});
