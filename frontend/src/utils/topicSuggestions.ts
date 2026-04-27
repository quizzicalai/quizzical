import type { TopicExample } from '../types/topicExamples';

function shuffleCopy<T>(items: readonly T[], randomFn: () => number): T[] {
  const output = [...items];
  for (let i = output.length - 1; i > 0; i -= 1) {
    const j = Math.floor(randomFn() * (i + 1));
    [output[i], output[j]] = [output[j], output[i]];
  }
  return output;
}

function normalizeCatalog(catalog: readonly TopicExample[]): TopicExample[] {
  const seenTopics = new Set<string>();
  const normalized: TopicExample[] = [];

  for (const item of catalog) {
    if (!item || typeof item.topic !== 'string' || typeof item.family !== 'string') {
      continue;
    }
    const topic = item.topic.trim();
    const family = item.family.trim();
    if (!topic || !family) {
      continue;
    }
    const key = topic.toLowerCase();
    if (seenTopics.has(key)) {
      continue;
    }
    seenTopics.add(key);
    normalized.push({ topic, family });
  }

  return normalized;
}

export function pickDiverseTopics(
  catalog: readonly TopicExample[],
  count: number,
  randomFn: () => number = Math.random,
): TopicExample[] {
  if (!Number.isFinite(count) || count <= 0) {
    return [];
  }

  const cleaned = normalizeCatalog(catalog);
  if (cleaned.length === 0) {
    return [];
  }

  const target = Math.min(cleaned.length, Math.floor(count));

  const byFamily = new Map<string, TopicExample[]>();
  for (const item of cleaned) {
    const bucket = byFamily.get(item.family) ?? [];
    bucket.push(item);
    byFamily.set(item.family, bucket);
  }

  const familyNames = shuffleCopy(Array.from(byFamily.keys()), randomFn);
  for (const familyName of familyNames) {
    const bucket = byFamily.get(familyName);
    if (bucket) {
      byFamily.set(familyName, shuffleCopy(bucket, randomFn));
    }
  }

  const selected: TopicExample[] = [];
  const selectedTopics = new Set<string>();

  let didAdd = true;
  while (selected.length < target && didAdd) {
    didAdd = false;
    for (const familyName of familyNames) {
      if (selected.length >= target) {
        break;
      }
      const bucket = byFamily.get(familyName);
      if (!bucket || bucket.length === 0) {
        continue;
      }
      const candidate = bucket.shift();
      if (!candidate) {
        continue;
      }
      const key = candidate.topic.toLowerCase();
      if (selectedTopics.has(key)) {
        continue;
      }
      selected.push(candidate);
      selectedTopics.add(key);
      didAdd = true;
    }
  }

  if (selected.length >= target) {
    return selected;
  }

  const leftovers = shuffleCopy(
    Array.from(byFamily.values()).flat(),
    randomFn,
  );
  for (const candidate of leftovers) {
    if (selected.length >= target) {
      break;
    }
    const key = candidate.topic.toLowerCase();
    if (selectedTopics.has(key)) {
      continue;
    }
    selected.push(candidate);
    selectedTopics.add(key);
  }

  return selected;
}
