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

function hashText(text: string): number {
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

function toQuizPrompt(rawTopic: string, family: string): string {
  const lower = rawTopic.toLowerCase();
  if (lower === 'lampshade' || lower.includes('lampshade')) {
    return 'Which lampshade style are you?';
  }
  if (lower.includes('myers briggs')) {
    return 'Which Myers-Briggs type are you?';
  }
  if (lower === 'doctors' || lower.includes('doctor')) {
    return 'What type of doctor fits your personality?';
  }
  if (lower === 'countries' || lower.includes('country')) {
    return 'Which country matches your personality?';
  }
  if (lower.includes('friends character')) {
    return 'Which Friends character are you?';
  }
  if (lower.includes('harry potter house')) {
    return 'Which Harry Potter house are you?';
  }

  const subject = sanitizeSubject(rawTopic);
  const displaySubject = subject
    ? subject.charAt(0).toUpperCase() + subject.slice(1)
    : subject;
  const templates = [
    `${displaySubject}: which one matches your personality?`,
    `${displaySubject}: which choice fits your vibe best?`,
    `${displaySubject}: which one reflects your energy?`,
    `${displaySubject}: which option feels most like you?`,
    `${displaySubject}: which path would be your alter ego?`,
    `${displaySubject}: which angle matches your mindset?`,
  ];
  const index = hashText(`${rawTopic}::${family}`) % templates.length;
  return templates[index];
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
    const topic = toQuizPrompt(item.topic, item.family);
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
