import type { TopicExample } from '../types/topicExamples';

const RAW_PREFIXES = [
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

function _hashText(text: string): number {
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

function sanitizeSubject(rawTopic: string): string {
  let subject = rawTopic.trim();
  for (const prefix of RAW_PREFIXES) {
    if (subject.toLowerCase().startsWith(prefix.toLowerCase())) {
      subject = subject.slice(prefix.length).trim();
      break;
    }
  }

  subject = subject.replace(/^Variation\s+\d+\s+of\s+/i, '').trim();
  subject = subject.replace(/\s+Changed the World$/i, '').trim();
  subject = subject.replace(/\s+Matters$/i, '').trim();
  return subject || rawTopic.trim();
}

function toTitleCase(text: string): string {
  return text
    .split(' ')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function toCloudTopic(rawTopic: string): string {
  const lower = rawTopic.toLowerCase();
  if (lower === 'lampshade' || lower.includes('lampshade')) {
    return 'Lampshade Styles';
  }
  if (lower.includes('myers briggs')) {
    return 'Myers-Briggs Types';
  }
  if (lower === 'doctors' || lower.includes('doctor')) {
    return 'Doctor Types';
  }
  if (lower === 'countries' || lower.includes('country')) {
    return 'Countries';
  }
  if (lower.includes('friends character')) {
    return 'Friends Characters';
  }
  if (lower.includes('harry potter house')) {
    return 'Harry Potter Houses';
  }

  let subject = sanitizeSubject(rawTopic)
    .replace(/[?.!]+$/g, '')
    .replace(/^which\s+/i, '')
    .replace(/^what\s+/i, '')
    .replace(/^who\s+/i, '')
    .replace(/^where\s+/i, '')
    .replace(/^how\s+/i, '')
    .replace(/\s+are you$/i, '')
    .replace(/\s+fits your personality$/i, '')
    .replace(/\s+matches your personality$/i, '')
    .replace(/\s+best matches your personality$/i, '')
    .replace(/\s+should you live in$/i, '')
    .trim();

  if (!subject) {
    subject = rawTopic.trim();
  }

  const normalized = subject.replace(/[:;,]+/g, ' ').replace(/\s+/g, ' ').trim();
  const words = normalized.split(' ').filter(Boolean);
  const maxWords = 4;
  const clipped = words.slice(0, maxWords).join(' ');
  return toTitleCase(clipped);
}

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
    const topic = toCloudTopic(item.topic);
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
