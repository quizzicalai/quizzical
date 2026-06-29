// PROTOTYPE-ONLY demo page (prototype/qa-image-enrichment).
// Renders the REAL QuestionView/AnswerGrid components with realistic
// agent-style Q&A so we can screenshot routed brand icons end-to-end.
// Reached at /dev/qa-icons (dev-only route). Gated behind VITE_PROTO_QA_ICONS
// for the icons themselves (the page still renders without the flag, showing
// the unchanged baseline — useful for before/after CLS comparison).

import React, { useState } from 'react';
import { QuestionView } from '../components/quiz/QuestionView';
import { QA_ICONS_ENABLED } from './QaIcon';
import type { Question } from '../types/quiz';

// A varied set of realistic, agent-generated-style questions (the kind the
// non-deterministic agent emits). Icons are resolved by the precomputed
// text->iconId binding inside QuestionView/AnswerGrid (no iconId set here on
// purpose, to exercise the routing-by-text path).
const DEMO_QUESTIONS: Question[] = [
  {
    id: 'q1',
    text: 'Which coffee drink matches your vibe?',
    answers: [
      { id: 'a1', text: 'Espresso' },
      { id: 'a2', text: 'Cold Brew' },
      { id: 'a3', text: 'A glass of wine' },
      { id: 'a4', text: 'A slice of pizza' },
    ],
  },
  {
    id: 'q2',
    text: 'What is your ideal vacation?',
    answers: [
      { id: 'b1', text: 'A beach resort by the ocean' },
      { id: 'b2', text: 'Hiking in the mountains' },
      { id: 'b3', text: 'Exploring a foreign city' },
      { id: 'b4', text: 'Going for a long bike ride' },
    ],
  },
  {
    id: 'q3',
    text: "What's your dream job?",
    answers: [
      { id: 'c1', text: 'Software engineer' },
      { id: 'c2', text: 'Doctor' },
      { id: 'c3', text: 'Musician' },
      { id: 'c4', text: 'Astronaut' },
    ],
  },
];

export function QaIconsDemoPage() {
  const [idx, setIdx] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const q = DEMO_QUESTIONS[idx];

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8">
      <div className="mb-6 rounded-xl border border-border bg-card p-4 text-sm text-muted">
        <strong className="text-fg">Q&amp;A image-enrichment prototype.</strong>{' '}
        Brand icons {QA_ICONS_ENABLED ? 'ON' : 'OFF'} (VITE_PROTO_QA_ICONS).
        Icons are precomputed (semantic NN router) and rendered inline (zero
        extra requests, reserved space). Decorative only.
        <div className="mt-3 flex gap-2">
          {DEMO_QUESTIONS.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => {
                setIdx(i);
                setSelected(null);
              }}
              className={
                'rounded-lg border px-3 py-1 text-xs ' +
                (i === idx
                  ? 'border-primary text-primary'
                  : 'border-border text-muted')
              }
            >
              Q{i + 1}
            </button>
          ))}
        </div>
      </div>

      <QuestionView
        question={q}
        onSelectAnswer={(id) => setSelected(id)}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={idx + 1}
        selectedAnswerId={selected}
      />
    </div>
  );
}

export default QaIconsDemoPage;
