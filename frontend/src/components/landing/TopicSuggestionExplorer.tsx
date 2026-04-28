import React, { useState } from 'react';
import topicExamplesCatalog from '../../data/topicExamples.json';
import type { TopicExample } from '../../types/topicExamples';
import { pickDiverseTopics } from '../../utils/topicSuggestions';

const DEFAULT_VISIBLE_TOPICS = 16;

export type TopicSuggestionExplorerProps = {
  onSelectTopic: (topic: string) => void;
};

const TopicSuggestionExplorer: React.FC<TopicSuggestionExplorerProps> = ({ onSelectTopic }) => {
  const catalog = topicExamplesCatalog as TopicExample[];
  const [suggestions, setSuggestions] = useState<TopicExample[]>(() =>
    pickDiverseTopics(catalog, DEFAULT_VISIBLE_TOPICS)
  );

  const handleShuffle = () => {
    setSuggestions(pickDiverseTopics(catalog, DEFAULT_VISIBLE_TOPICS));
  };

  return (
    <section className="lp-topic-explorer mt-5" aria-label="Suggested quiz topics">
      <div className="mb-2 text-left sm:text-center">
        <h2 className="text-sm font-semibold tracking-tight text-fg">Need inspiration?</h2>
        <p className="mt-1 text-xs text-muted">Tap a topic to fill the field instantly.</p>
      </div>

      <div className="flex items-center justify-end mb-2">
        <button
          type="button"
          className="lp-topic-refresh"
          onClick={handleShuffle}
          aria-label="Shuffle ideas"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" className="lp-topic-refresh-icon">
            <path
              d="M4 6h2.7c1.5 0 2.9.7 3.8 1.8l1.2 1.5M4 18h2.7c1.5 0 2.9-.7 3.8-1.8l6.8-8.4c1-1.1 2.3-1.8 3.8-1.8H20M20 6v4m0-4h-4M20 18v-4m0 4h-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="lp-topic-refresh-label">Shuffle ideas</span>
        </button>
      </div>

      <div className="lp-topic-chip-cloud">
        {suggestions.map((item) => (
          <button
            key={`${item.family}:${item.topic}`}
            type="button"
            className="lp-topic-chip"
            onClick={() => onSelectTopic(item.topic)}
            data-testid="topic-suggestion-chip"
            aria-label={`Use topic ${item.topic}`}
          >
            {item.topic}
          </button>
        ))}
      </div>
    </section>
  );
};

export default TopicSuggestionExplorer;
