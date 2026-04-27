import React, { useMemo, useState } from 'react';
import topicExamplesCatalog from '../../data/topicExamples.json';
import type { TopicExample } from '../../types/topicExamples';
import { pickDiverseTopics } from '../../utils/topicSuggestions';

const DEFAULT_VISIBLE_TOPICS = 12;

export type TopicSuggestionExplorerProps = {
  onSelectTopic: (topic: string) => void;
  title?: string;
  subtitle?: string;
};

const TopicSuggestionExplorer: React.FC<TopicSuggestionExplorerProps> = ({
  onSelectTopic,
  title = 'Need inspiration? Try a topic spark',
  subtitle = 'Randomized, diverse ideas you can click once and generate instantly.',
}) => {
  const catalog = topicExamplesCatalog as TopicExample[];
  const [refreshNonce, setRefreshNonce] = useState(0);

  const suggestions = useMemo(() => {
    const firstPass = pickDiverseTopics(catalog, DEFAULT_VISIBLE_TOPICS);
    return firstPass;
  }, [catalog, refreshNonce]);

  const handleShuffle = () => {
    setRefreshNonce((prev) => prev + 1);
  };

  return (
    <section className="lp-topic-explorer mt-5" aria-label="Suggested quiz topics">
      <div className="flex items-center justify-between gap-3 mb-2">
        <h2 className="text-sm sm:text-[0.95rem] font-semibold text-fg leading-tight m-0">
          {title}
        </h2>
        <button
          type="button"
          className="lp-topic-refresh"
          onClick={handleShuffle}
          aria-label="Shuffle ideas"
        >
          Shuffle ideas
        </button>
      </div>

      <p className="text-xs sm:text-sm text-muted mb-3">{subtitle}</p>

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
