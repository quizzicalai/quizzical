import React from 'react';
import topicExamplesCatalog from '../../data/topicExamples.json';
import type { TopicExample } from '../../types/topicExamples';
import { ShuffleIcon } from '../../assets/icons/ShuffleIcon';

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

/**
 * The 100-ish most recognizable personality-quiz topics. These are the
 * suggestions that surface in the "Popular" subsection: at any given moment
 * three are shown, chosen at random from this list and reshuffled when the
 * user clicks "Load more". These are deliberately distinct from the wider
 * `TOPIC_POOL` so the Popular row always feels like the greatest hits.
 */
const POPULAR_TOPICS: ReadonlyArray<string> = Object.freeze([
  'Friends character',
  'Myers-Briggs type',
  'Hogwarts house',
  'Disney princess',
  'Greek god',
  'Marvel hero',
  'Star Wars Jedi',
  'Game of Thrones house',
  'The Office character',
  'Studio Ghibli character',
  'Pixar character',
  'Pokémon starter',
  'Avatar bending nation',
  'Percy Jackson cabin',
  'Hunger Games district',
  'Dungeons & Dragons class',
  'Star Trek crew member',
  'Doctor Who Doctor',
  'Lord of the Rings race',
  'How I Met Your Mother character',
  'Seinfeld character',
  'Brooklyn Nine-Nine character',
  'Parks and Recreation character',
  'Stranger Things character',
  'Breaking Bad character',
  'Better Call Saul character',
  'Succession character',
  'Ted Lasso character',
  'Schitt\'s Creek character',
  'Game of Thrones character',
  'House of the Dragon character',
  'Wheel of Time Ajah',
  'Harry Potter character',
  'Disney villain',
  'Pixar movie',
  'Studio Ghibli movie',
  'Wes Anderson movie',
  'Quentin Tarantino character',
  'Marvel villain',
  'DC superhero',
  'X-Men character',
  'Avengers hero',
  'Spider-Man villain',
  'Mortal Kombat fighter',
  'Street Fighter character',
  'Super Smash Bros fighter',
  'Mario Kart racer',
  'Animal Crossing villager',
  'Genshin Impact element',
  'Pokémon type',
  'Final Fantasy character',
  'Legend of Zelda character',
  'Sonic the Hedgehog character',
  'Kingdom Hearts character',
  'Disney Channel original star',
  'High School Musical character',
  'Mean Girls character',
  'Clueless character',
  'Twilight team',
  'Hunger Games character',
  'Maze Runner faction',
  'Divergent faction',
  'Percy Jackson character',
  'Magic: The Gathering color',
  'Tarot card',
  'Enneagram type',
  'Love language',
  'DISC personality type',
  'Astrological sign',
  'Chinese zodiac animal',
  'Hogwarts subject',
  'Greek muse',
  'Norse god',
  'Egyptian deity',
  'Roman emperor',
  'US president',
  'Founding Father',
  'Renaissance painter',
  'Impressionist painter',
  'Pop art icon',
  'Beatles song',
  'Taylor Swift era',
  'Beyoncé era',
  'Drake song',
  'Hip-hop legend',
  'NBA legend',
  'NFL quarterback',
  'Premier League club',
  'Soccer position',
  'Olympic sport',
  'Yoga style',
  'Coffee order',
  'Pizza topping',
  'Ice cream flavor',
  'Cocktail',
  'Wine grape',
  'Cheese',
  'Cuisine',
  'Cottagecore aesthetic',
  'Dark academia archetype',
  'Travel destination',
  'European city',
  'Sitcom decade',
]);

// Number of example chips rendered on the landing page. We deliberately
// show plenty of variety on every screen size so the cloud feels rich on
// phones (where each chip is a small pill) as well as tall desktop layouts.
const RENDERED_CHIP_COUNT = 72;

// The "Popular" subsection is always exactly three chips.
const POPULAR_CHIP_COUNT = 3;

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
// eslint-disable-next-line react-refresh/only-export-components
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
  // A nonce that we bump on every shuffle click so BOTH the "Popular" and
  // "Random" lists can be regenerated on demand without remounting the section.
  const [shuffleNonce, setShuffleNonce] = React.useState(0);

  // Three popular picks, reshuffled each time the user clicks "Load more".
  const popularTopics = React.useMemo(
    () => pickRandomTopics(POPULAR_TOPICS, POPULAR_CHIP_COUNT),
    // shuffleNonce is the explicit re-roll trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shuffleNonce],
  );

  // The wider random cloud, randomized once per page load and once per shuffle.
  const suggestedTopics = React.useMemo(
    () => pickRandomTopics(TOPIC_POOL, RENDERED_CHIP_COUNT),
    // shuffleNonce is the explicit re-roll trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shuffleNonce],
  );

  const handleShuffle = React.useCallback(() => {
    setShuffleNonce((n) => n + 1);
  }, []);

  const renderChip = (topic: string) => (
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
  );

  return (
    <section className="lp-topic-explorer mt-3" aria-label="Suggested quiz topics">
      {/* "Popular" — exactly three of the most recognizable personality quizzes.
          Visually distinct from the wider random cloud below via a small label
          header. Reshuffles together with the random set on "Load more". */}
      <div
        role="group"
        aria-label="Popular quiz topics"
        data-testid="topic-suggestion-popular"
      >
        {/* AC-UX-2026-05-25-PART3 item 2 — left-aligned section headers
            with a small mobile inset (pl-2). UI-modernization items 8+9:
            dropped the desktop `lg:pl-8` so the labels align to the centered
            36rem form axis instead of drifting right; tightened tracking
            0.18em → 0.1em; and recolored off the hardcoded `text-slate-500`
            literal onto the --color-text-secondary token. (text-muted /
            slate-400 was the intended softer target but renders only 2.56:1
            on the white card and FAILS WCAG AA for this 12px label, so per
            the hitlist the color is routed through --color-text-secondary =
            slate-600 = 7.58:1.) */}
        {/* h2 (was h3): now that the hero H1 is mounted (item 4) the section
            labels must be the next level down (h1 → h2) or axe-core flags a
            heading-order skip. These are the only other headings on the page. */}
        <h2
          data-testid="topic-suggestion-popular-heading"
          className="mb-2 pl-2 text-left text-xs font-semibold uppercase tracking-[0.1em]"
          style={{ color: 'rgb(var(--color-text-secondary, 71 85 105))' }}
        >
          Popular
        </h2>
        <div className="lp-topic-chip-cloud lp-topic-chip-cloud--popular">
          {popularTopics.map(renderChip)}
        </div>
      </div>

      {/* "Random" — the wider sampled cloud. */}
      <div
        role="group"
        aria-label="Random quiz topics"
        data-testid="topic-suggestion-random"
        className="mt-6"
      >
        <h2
          data-testid="topic-suggestion-random-heading"
          className="mb-2 pl-2 text-left text-xs font-semibold uppercase tracking-[0.1em]"
          style={{ color: 'rgb(var(--color-text-secondary, 71 85 105))' }}
        >
          Random
        </h2>
        <div className="lp-topic-chip-cloud">
          {suggestedTopics.map(renderChip)}
        </div>
      </div>

      <div
        className="lp-topic-shuffle-row mt-3 flex justify-center"
        data-testid="topic-suggestion-shuffle-row"
      >
        <button
          type="button"
          onClick={handleShuffle}
          aria-label="Load more suggestions"
          title="Load more suggestions"
          data-testid="topic-suggestion-shuffle"
          className="lp-topic-refresh"
        >
          <ShuffleIcon
            className="lp-topic-refresh-icon"
            aria-hidden="true"
            focusable="false"
          />
          <span className="lp-topic-refresh-label">Load more</span>
        </button>
      </div>
    </section>
  );
};

export default TopicSuggestionExplorer;
