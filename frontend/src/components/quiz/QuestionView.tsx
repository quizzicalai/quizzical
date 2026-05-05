// frontend/src/components/quiz/QuestionView.tsx
import React, { useEffect, useRef, useState } from 'react';
import { AnswerGrid } from './AnswerGrid';
import { ThinkingIndicator } from './ThinkingIndicator';
import type { Question } from '../../types/quiz';

// AC-PROD-R7-TW-POOL-1 — placeholder phrase pool the FE cycles through
// while waiting for the agent's next step. >= 50 distinct phrases so the
// row never feels stale on long generations. Same narrative voice as the
// BE `ALL_NARROWING_PHRASES` pool (see
// `backend/app/agent/progress_phrases.py`).
// eslint-disable-next-line react-refresh/only-export-components
export const THINKING_PHRASES: readonly string[] = [
  'Thinking…',
  'Weighing your answer…',
  'Looking for patterns…',
  'Cross-checking clues…',
  'Narrowing the field…',
  'Sketching a hypothesis…',
  'Picking the next angle…',
  'Comparing your choices…',
  'Listening between the lines…',
  'Refining the read…',
  'Lining up candidates…',
  'Trying a fresh angle…',
  'Reading the room…',
  'Connecting the dots…',
  'Reviewing the latest signal…',
  'Trimming the long shots…',
  'Updating my mental model…',
  'Pulling on a loose thread…',
  'Auditioning a new question…',
  'Following the through-line…',
  'Spotting a tell…',
  'Cross-referencing the clues…',
  'Filing that away…',
  'Considering the opposite…',
  'Filtering the noise…',
  'Stress-testing my hunch…',
  'Mapping your style…',
  'Looking one move ahead…',
  'Tightening the focus…',
  'Sharpening the question…',
  'Watching for contradictions…',
  'Letting the data settle…',
  'Sketching the next prompt…',
  'Calibrating difficulty…',
  'Hunting for a tiebreaker…',
  'Lining up follow-ups…',
  'Playing devil\u2019s advocate…',
  'Searching for an outlier…',
  'Reframing the picture…',
  'Picking the cleanest angle…',
  'Re-weighing the candidates…',
  'Drafting the next move…',
  'Looking for a fresh signal…',
  'Splitting the field…',
  'Probing a soft spot…',
  'Listening for the strongest beat…',
  'Threading the needle…',
  'Fine-tuning the read…',
  'Choosing carefully…',
  'Almost there…',
  'Locking it in…',
];

// AC-PROD-R7-TW-POOL-2 — separate pool for the final-profile-generation
// phase. Phrases focus on building/writing the profile rather than asking
// another question.
// eslint-disable-next-line react-refresh/only-export-components
export const FINALIZING_PHRASES: readonly string[] = [
  'Building your profile…',
  'Connecting your answers…',
  'Sketching the portrait…',
  'Choosing your match…',
  'Polishing the verdict…',
  'Composing your write-up…',
  'Lining up the highlights…',
  'Naming the pattern…',
  'Capturing your tone…',
  'Drafting the description…',
  'Cross-checking the result…',
  'Tightening the language…',
  'Tying it all together…',
  'Adding the finishing touches…',
  'Almost ready to reveal…',
  'Wrapping it up…',
];

// AC-PROD-R13-ROTATE-1 — rotate every 3s (was 2.5s) per UX request so
// the user has a beat to actually read each phrase before it changes.
const ROTATE_INTERVAL_MS = 3000;

type QuestionViewProps = {
  question: Question | null;
  onSelectAnswer: (answerId: string) => void;
  isLoading: boolean;
  inlineError: string | null;
  onRetry: () => void;
  /**
   * 1-based ordinal of the current question. The agent ends the quiz on
   * either max-questions OR a confidence threshold, so we deliberately do
   * not show a denominator like "of 20" — that would mislead. Falls back
   * to question.questionNumber when omitted.
   */
  questionNumber?: number;
  /**
   * Short status string ("I'm narrowing in…") shown in the upper-right
   * thinking row alongside the spinner / ∴ glyph. Falls back to
   * question.progressPhrase when omitted.
   */
  progressPhrase?: string;
  /**
   * Which placeholder pool to cycle while `isLoading` and no LLM phrase
   * has arrived. AC-PROD-R7-TW-POOL-2 — use `'finalizing'` once the agent
   * is generating the final user profile so the visible status reflects
   * profile-writing rather than next-question planning.
   */
  mode?: 'thinking' | 'finalizing';
  selectedAnswerId?: string | null;
};

export function QuestionView({
  question,
  onSelectAnswer,
  isLoading,
  inlineError,
  onRetry,
  questionNumber,
  progressPhrase,
  mode = 'thinking',
  selectedAnswerId,
}: QuestionViewProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (question?.id) {
      headingRef.current?.focus();
    }
  }, [question?.id]);

  // Resolve progress fields up-front so the rotation hooks below can be
  // declared unconditionally (lint: react-hooks/rules-of-hooks). All values
  // are safe to compute even when `question` is null.
  const phrase = (progressPhrase ?? question?.progressPhrase ?? '').trim();

  // AC-PROD-R6-FE-ROTATE-1/2 + AC-PROD-R7-TW-POOL-2 — cycle the curated
  // phrase pool every ROTATE_INTERVAL_MS while loading and no upstream
  // phrase is available. The pool depends on `mode` so finalizing reads
  // as profile-writing rather than next-question planning. The interval
  // is cleared whenever loading stops, an LLM phrase arrives, the mode
  // changes, or the component unmounts.
  const activePool = mode === 'finalizing' ? FINALIZING_PHRASES : THINKING_PHRASES;
  const [rotatedIndex, setRotatedIndex] = useState(0);
  const useRotation = isLoading && !phrase;
  useEffect(() => {
    if (!useRotation) {
      setRotatedIndex(0);
      return;
    }
    const id = window.setInterval(() => {
      setRotatedIndex((i) => (i + 1) % activePool.length);
    }, ROTATE_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [useRotation, activePool]);

  if (!question) {
    return null;
  }

  const number =
    typeof questionNumber === 'number' && questionNumber > 0
      ? Math.floor(questionNumber)
      : typeof question.questionNumber === 'number' && question.questionNumber > 0
        ? Math.floor(question.questionNumber)
        : null;

  // AC-PROD-R13-VIS-1 — the thinking row ALWAYS renders. In idle the
  // indicator is two static dots (acts as a quiet AI presence marker);
  // while loading the same two dots spin and the phrase rotates. Always
  // rendering the row also avoids any CLS when a phrase arrives async.
  const displayPhrase = phrase || (isLoading ? activePool[rotatedIndex] : '');

  return (
    <div className="max-w-3xl mx-auto text-center">
      {/* Top status row: AI thinking widget + italic phrase, top-right.
          Spinner while the agent is loading the next step; two static
          dots when idle (always visible per AC-PROD-R13-VIS-1). */}
      <div
        className="mb-5 flex items-center justify-end gap-2 min-h-[1.25rem]"
        data-testid="quiz-thinking-row"
      >
        <ThinkingIndicator
          thinking={isLoading}
          ariaLabel={displayPhrase || 'Thinking'}
        />
        <span
          // AC-PROD-R8-TEXT-1 — dark grey, never reads as black. Use
          // slate-500 explicitly so a parent's `text-fg` cascade does
          // not bleed through.
          className="text-xs sm:text-sm italic text-slate-500"
          data-testid="quiz-progress-phrase"
          aria-live="polite"
        >
          {displayPhrase}
        </span>
      </div>

      {/* Question text — sized down per UX feedback (was text-2xl/3xl). */}
      <h2
        ref={headingRef}
        tabIndex={-1}
        aria-live="polite"
        className="font-display text-xl sm:text-2xl font-semibold tracking-tight text-fg mb-6 outline-none"
      >
        {question.text}
      </h2>

      {/* Answers (kept: 1 col → 2 cols responsive) */}
      <AnswerGrid
        answers={question.answers}
        onSelect={onSelectAnswer}
        disabled={isLoading}
        selectedId={selectedAnswerId}
      />

      {/* Error (if any) */}
      {inlineError && (
        <div className="mt-6" role="alert">
          <p className="text-red-600 mb-3">{inlineError}</p>
          {onRetry && (
            <button
              type="button"
              className="px-4 py-2 rounded-lg bg-fg text-card hover:opacity-90 transition"
              onClick={onRetry}
            >
              Try Again
            </button>
          )}
        </div>
      )}

      {/* Bottom: just the current question ordinal — no denominator. */}
      {number !== null && (
        <div
          className="mt-8 text-xs font-medium uppercase tracking-wide text-muted/90"
          data-testid="quiz-question-ordinal"
        >
          Question {number}
        </div>
      )}
    </div>
  );
}
