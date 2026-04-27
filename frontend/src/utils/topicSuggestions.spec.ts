import { describe, it, expect } from 'vitest';
import type { TopicExample } from '../types/topicExamples';
import { pickDiverseTopics } from './topicSuggestions';

const CATALOG: TopicExample[] = [
  { topic: 'Which type of doctor are you?', family: 'careers' },
  { topic: 'Which country matches your personality?', family: 'geography' },
  { topic: 'Which Harry Potter house are you?', family: 'pop-culture' },
  { topic: 'Which lamp are you?', family: 'objects' },
  { topic: 'Which dog breed are you?', family: 'animals' },
  { topic: 'Which philosopher matches your mindset?', family: 'ideas' },
  { topic: 'Which coffee drink are you?', family: 'food' },
  { topic: 'Which board game style are you?', family: 'hobbies' },
  { topic: 'Which startup role are you?', family: 'careers' },
  { topic: 'Which city should you live in?', family: 'geography' },
];

describe('pickDiverseTopics', () => {
  it('returns unique topics and requested count when enough data exists', () => {
    const result = pickDiverseTopics(CATALOG, 8, () => 0.42);
    expect(result).toHaveLength(8);
    expect(new Set(result.map((item) => item.topic)).size).toBe(8);
  });

  it('prioritizes family diversity before filling remaining slots', () => {
    const result = pickDiverseTopics(CATALOG, 8, () => 0.17);
    const distinctFamilies = new Set(result.map((item) => item.family));
    expect(distinctFamilies.size).toBeGreaterThanOrEqual(6);
  });

  it('returns all unique topics if requested count exceeds catalog size', () => {
    const result = pickDiverseTopics(CATALOG, 100, () => 0.75);
    expect(result).toHaveLength(CATALOG.length);
  });
});
