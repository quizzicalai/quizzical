import { describe, it, expect } from 'vitest';
import {
  isWrappedSynopsis,
  isWrappedQuestion,
  isWrappedCharacters,
  isRawQuestion,
  isRawSynopsis,
  toUiAnswers,
  toUiQuestionFromApi,
  toUiCharacters,
  toUiResult,
  type WrappedSynopsis,
  type WrappedQuestion,
  type WrappedCharacters,
} from './quizGuards';

// -------------------------
// Guards: wrapped payloads
// -------------------------
describe('quizGuards: wrapped guards', () => {
  it('isWrappedSynopsis: true only for { type:"synopsis", data: valid Synopsis }', () => {
    const good: WrappedSynopsis = {
      type: 'synopsis',
      data: { title: 'T', summary: 'S' }, // no answers array, has title+summary
    };
    expect(isWrappedSynopsis(good)).toBe(true);

    // wrong type tag
    expect(isWrappedSynopsis({ type: 'question', data: { title: 'T', summary: 'S' } })).toBe(false);
    // missing data
    expect(isWrappedSynopsis({ type: 'synopsis' } as any)).toBe(false);
    // invalid data (answers array makes it a question)
    expect(isWrappedSynopsis({ type: 'synopsis', data: { title: 'T', summary: 'S', answers: [] } })).toBe(false);
    // null/undefined
    expect(isWrappedSynopsis(null)).toBe(false);
    expect(isWrappedSynopsis(undefined)).toBe(false);
    // primitives
    expect(isWrappedSynopsis('synopsis' as any)).toBe(false);
  });

  it('isWrappedQuestion: true only for { type:"question", data: valid Question }', () => {
    const good: WrappedQuestion = {
      type: 'question',
      data: { id: 'q1', text: 'Q?', answers: [] },
    };
    expect(isWrappedQuestion(good)).toBe(true);

    // wrong type tag
    expect(isWrappedQuestion({ type: 'synopsis', data: { text: 'Q?', answers: [] } })).toBe(false);
    // missing answers array
    expect(isWrappedQuestion({ type: 'question', data: { text: 'Q?' } })).toBe(false);
    // null/undefined
    expect(isWrappedQuestion(null)).toBe(false);
    expect(isWrappedQuestion(undefined)).toBe(false);
    // primitives
    expect(isWrappedQuestion(123 as any)).toBe(false);
  });

  it('isWrappedCharacters: requires type:"characters" and array data, tolerant to element shape', () => {
    const good: WrappedCharacters = { type: 'characters', data: [{ name: 'A', shortDescription: '', profileText: '' }] };
    expect(isWrappedCharacters(good)).toBe(true);

    // wrong type tag
    expect(isWrappedCharacters({ type: 'synopsis', data: [] })).toBe(false);
    // data not an array
    expect(isWrappedCharacters({ type: 'characters', data: {} })).toBe(false);
    // null/undefined
    expect(isWrappedCharacters(null)).toBe(false);
    expect(isWrappedCharacters(undefined)).toBe(false);
  });
});

// -------------------------
// Guards: raw shapes
// -------------------------
describe('quizGuards: raw guards', () => {
  it('isRawQuestion: true if answers is an array (even empty)', () => {
    expect(isRawQuestion({ text: 'Q', answers: [] })).toBe(true);
    expect(isRawQuestion({ text: 'Q', answers: [{ id: 'a', text: 'A' }] })).toBe(true);
  });

  it('isRawQuestion: false otherwise', () => {
    expect(isRawQuestion({ text: 'Q' })).toBe(false);
    expect(isRawQuestion({ text: 'Q', answers: 'nope' })).toBe(false);
    expect(isRawQuestion(null)).toBe(false);
    expect(isRawQuestion(undefined)).toBe(false);
    expect(isRawQuestion('answers' as any)).toBe(false);
  });

  it('isRawSynopsis: needs title & summary strings and must NOT have answers array', () => {
    expect(isRawSynopsis({ title: 'T', summary: 'S' })).toBe(true);
    // missing fields
    expect(isRawSynopsis({ title: 'T' })).toBe(false);
    expect(isRawSynopsis({ summary: 'S' })).toBe(false);
    // wrong types
    expect(isRawSynopsis({ title: 1, summary: 'S' })).toBe(false);
    expect(isRawSynopsis({ title: 'T', summary: 2 })).toBe(false);
    // has answers array ⇒ not a synopsis
    expect(isRawSynopsis({ title: 'T', summary: 'S', answers: [] })).toBe(false);
  });
});

// -------------------------
// Normalizers: toUiAnswers
// -------------------------
describe('toUiAnswers', () => {
  it('converts strings into Answer[] with ids and imageAlt from text', () => {
    const out = toUiAnswers(['A', 'B']);
    expect(out).toEqual([
      { id: 'opt-0', text: 'A', imageUrl: undefined, imageAlt: 'A' },
      { id: 'opt-1', text: 'B', imageUrl: undefined, imageAlt: 'B' },
    ]);
  });

  it('accepts dicts with {text} or {label}; preserves imageUrl and image_url', () => {
    const out = toUiAnswers([
      { text: 'T1', imageUrl: 'x.jpg' },
      { label: 'L2', image_url: 'y.jpg' },
    ]);
    expect(out[0]).toMatchObject({ id: 'opt-0', text: 'T1', imageUrl: 'x.jpg', imageAlt: 'T1' });
    expect(out[1]).toMatchObject({ id: 'opt-1', text: 'L2', imageUrl: 'y.jpg', imageAlt: 'L2' });
  });

  it('handles unknown/empty options by stringifying and trimming; imageAlt becomes text or undefined', () => {
    const out = toUiAnswers([{}, ' ', null as any, undefined as any]);
    // {} → String({}) = "[object Object]"
    expect(out[0]).toMatchObject({ id: 'opt-0', text: '[object Object]', imageAlt: '[object Object]' });
    // ' ' → trimmed to '' so imageAlt becomes undefined
    expect(out[1]).toMatchObject({ id: 'opt-1', text: '', imageAlt: undefined });
    // null/undefined coalesce to '' then trim → '' (imageAlt becomes undefined)
    expect(out[2]).toMatchObject({ id: 'opt-2', text: '', imageAlt: undefined });
    expect(out[3]).toMatchObject({ id: 'opt-3', text: '', imageAlt: undefined });
  });

  it('non-array input yields empty list', () => {
    expect(toUiAnswers(null as any)).toEqual([]);
    expect(toUiAnswers('nope' as any)).toEqual([]);
  });
});

// -------------------------
// Normalizer: toUiQuestionFromApi
// -------------------------
describe('toUiQuestionFromApi', () => {
  it('accepts text | question_text | questionText; converts options to answers; carries image', () => {
    const a = toUiQuestionFromApi({
      id: 'q1',
      text: 'T',
      options: ['A'],
      imageUrl: 'a.png',
      imageAlt: 'alt',
    });
    expect(a).toEqual({
      id: 'q1',
      text: 'T',
      imageUrl: 'a.png',
      imageAlt: 'alt',
      answers: [{ id: 'opt-0', text: 'A', imageUrl: undefined, imageAlt: 'A' }],
    });

    const b = toUiQuestionFromApi({
      question_text: 'Legacy',
      options: [{ label: 'X' }],
      image_url: 'b.png',
    });
    expect(b.text).toBe('Legacy');
    expect(b.imageUrl).toBe('b.png');
    expect(b.answers[0]).toMatchObject({ text: 'X' });

    const c = toUiQuestionFromApi({
      questionText: 'Another legacy',
      options: [],
    });
    expect(c.text).toBe('Another legacy');
    expect(c.answers).toEqual([]);
  });

  it('tolerates missing fields and non-array options (defaults apply)', () => {
    const x = toUiQuestionFromApi({});
    expect(x).toMatchObject({
      id: undefined,
      text: '',
      imageUrl: undefined,
      imageAlt: undefined,
      answers: [],
    });

    const y = toUiQuestionFromApi({ options: 'not-an-array' });
    expect(y.answers).toEqual([]);
  });
});

// -------------------------
// Normalizer: toUiCharacters
// -------------------------
describe('toUiCharacters', () => {
  it('maps snake_case → camelCase and fills missing strings', () => {
    const out = toUiCharacters([
      { name: 'A', short_description: 'sd', profile_text: 'pt', image_url: 'img.png' },
      { name: 'B', shortDescription: 'sd2', profileText: 'pt2' }, // camel already
      {}, // empty object → empty strings
    ] as any[]);

    expect(out[0]).toEqual({
      name: 'A',
      shortDescription: 'sd',
      profileText: 'pt',
      imageUrl: 'img.png',
    });
    expect(out[1]).toEqual({
      name: 'B',
      shortDescription: 'sd2',
      profileText: 'pt2',
      imageUrl: undefined,
    });
    expect(out[2]).toEqual({
      name: '',
      shortDescription: '',
      profileText: '',
      imageUrl: undefined,
    });
  });

  it('non-array input produces []', () => {
    expect(toUiCharacters(null as any)).toEqual([]);
    expect(toUiCharacters('no' as any)).toEqual([]);
  });
});

// -------------------------
// Normalizer: toUiResult
// -------------------------
describe('toUiResult', () => {
  it('maps API final result to UI; null imageUrl → undefined; traits pass-through only when array', () => {
    const out = toUiResult({
      title: 'Architect',
      imageUrl: null, // becomes undefined
      description: 'desc',
      traits: [{ id: 't1', label: 'Bold' }],
      shareUrl: 'https://x',
    });

    expect(out).toEqual({
      profileTitle: 'Architect',
      imageUrl: undefined,
      imageAlt: 'Architect',
      summary: 'desc',
      traits: [{ id: 't1', label: 'Bold' }],
      shareUrl: 'https://x',
    });
  });

  it('tolerates missing/invalid fields; coerces to safe UI shape', () => {
    const out = toUiResult({});
    expect(out).toEqual({
      profileTitle: '',
      imageUrl: undefined,
      imageAlt: undefined,
      summary: '',
      traits: undefined,
      shareUrl: undefined,
    });

    const out2 = toUiResult({ title: 'T', description: 'D', traits: 'nope' });
    expect(out2.traits).toBeUndefined();
  });
});
