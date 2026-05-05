import React from 'react';
import topicExamplesCatalog from '../../data/topicExamples.json';
import type { TopicExample } from '../../types/topicExamples';

/**
 * Curated noun-phrases that read naturally inside the
 * "Which ____ am I?" landing composition. The chip's visible text is the
 * full question; the value passed to onSelectTopic is the bare noun phrase
 * so it slots straight into the input field.
 */
const CURATED_SEED_TOPICS: ReadonlyArray<string> = [
  'Hogwarts house',
  'Disney princess',
  'Greek god',
  'Marvel hero',
  'Pokémon starter',
  'Friends character',
  'Star Wars Jedi',
  'Studio Ghibli character',
  'Office character',
  'Game of Thrones house',
  'Pixar character',
  'Myers-Briggs type',
  'Beatles song',
  'Renaissance painter',
  'US president',
  'Pro tennis player',
  '90s sitcom',
  'Dungeons & Dragons class',
];

// Number of example chips rendered on the landing page. We deliberately
// show plenty of variety on every screen size so the cloud feels rich on
// phones (where each chip is a small pill) as well as tall desktop layouts.
const RENDERED_CHIP_COUNT = 72;

const RAW_PREFIXES: ReadonlyArray<string> = [
  'Exploring ',
  'History of ',
  'Future of ',
  'The Best ',
  'Understanding ',
  'Evolution of ',
  'Principles of ',
  'Mastering ',
  'Fundamentals of ',
  'The Secret of ',
  'Innovations in ',
  'World of ',
  'Diversity in ',
  'Challenges of ',
  'Impact of ',
  'Advancements in ',
  'Analysis of ',
  'Comprehensive Guide to ',
  'Beginners Guide to ',
  'Expert Tips for ',
  'Why ',
  'How ',
  'Famous ',
  'Unique ',
  'Modern ',
  'Ancient ',
  'Regional ',
  'Global ',
  'The Art of ',
  'The Science of ',
];


function standardizeCapitalization(topic: string): string {
  const value = topic.replace(/\s+/g, ' ').trim();
  if (!value) return '';
  const first = value.charAt(0);
  const upper = first.toLocaleUpperCase();
  return first === upper ? value : `${upper}${value.slice(1)}`;
}

function extractBaseTopic(raw: string): string {
  const trimmed = raw.replace(/\s+/g, ' ').trim();
  if (!trimmed) return '';
  for (const prefix of RAW_PREFIXES) {
    if (trimmed.startsWith(prefix)) {
      return trimmed.slice(prefix.length).trim();
    }
  }
  return trimmed;
}

function buildTopicPool(): ReadonlyArray<string> {
  const seen = new Set<string>();
  const out: string[] = [];

  for (const topic of CURATED_SEED_TOPICS) {
    const value = standardizeCapitalization(topic);
    const key = value.toLowerCase();
    if (!value || seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }

  for (const row of topicExamplesCatalog as TopicExample[]) {
    if (!row || typeof row.topic !== 'string') continue;
    const value = standardizeCapitalization(extractBaseTopic(row.topic));
    const key = value.toLowerCase();
    if (!value) continue;
    if (!seen.has(key)) {
      seen.add(key);
      out.push(value);
    }
  }

  return Object.freeze(out);
}

const TOPIC_POOL: ReadonlyArray<string> = buildTopicPool();

// Exposed for tests/observability: chip suggestions are sampled from 3,000+ topics.
export const TOPIC_POOL_SIZE = TOPIC_POOL.length;

function pickRandomTopics(
  topics: ReadonlyArray<string>,
  count: number,
  randomFn: () => number = Math.random,
): ReadonlyArray<string> {
  if (topics.length <= count) return topics;
  const copy = [...topics];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(randomFn() * (i + 1));
    const tmp = copy[i];
    copy[i] = copy[j];
    copy[j] = tmp;
  }
  return copy.slice(0, count);
}

export type TopicSuggestionExplorerProps = {
  onSelectTopic: (topic: string) => void;
};

const TopicSuggestionExplorer: React.FC<TopicSuggestionExplorerProps> = ({ onSelectTopic }) => {
  // Randomized once per page load so the suggestions feel fresh but stable while typing.
  const suggestedTopics = React.useMemo(
    () => pickRandomTopics(TOPIC_POOL, RENDERED_CHIP_COUNT),
    [],
  );

  return (
    <section className="lp-topic-explorer mt-8" aria-label="Suggested quiz topics">
      <div className="lp-topic-chip-cloud">
        {suggestedTopics.map((topic) => (
          <button
            key={topic}
            type="button"
            className="lp-topic-chip"
            onClick={() => onSelectTopic(topic)}
            data-testid="topic-suggestion-chip"
            aria-label={`Use topic ${topic}`}
          >
            <span className="lp-topic-chip-prefix">Which</span>
            <span className="lp-topic-chip-noun">{topic}</span>
            <span className="lp-topic-chip-suffix">am I?</span>
          </button>
        ))}
      </div>
    </section>
  );
};

export default TopicSuggestionExplorer;
