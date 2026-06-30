// frontend/src/components/quiz/QuestionView.tsx
import React, { useEffect, useRef, useState } from 'react';
import { AnswerGrid } from './AnswerGrid';
import { ThinkingIndicator } from './ThinkingIndicator';
import type { Question } from '../../types/quiz';
import { useFeatures } from '../../context/ConfigContext';
import { safeImageUrl } from '../../utils/safeImageUrl';
import { QuestionImage } from './QuestionImage';

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

// AC-UX-2026-05-25-PART2 item 6 — short, playful, first-person filler the
// FE rotates through every ROTATE_INTERVAL_MS while the agent is actively
// thinking about the NEXT question. Voice = warm, curious, occasionally
// cheeky; max ~6 words so the row reads at a glance. Intentionally large
// pool (>= 100) so users never see the same line twice in a session.
// eslint-disable-next-line react-refresh/only-export-components
export const ACTIVE_THINKING_PHRASES: readonly string[] = [
  'Hmmm…',
  'Interesting, let me think…',
  'Ooh, juicy answer…',
  'One sec—that\u2019s a juicy one…',
  'Okay, okay…',
  'Well, well, well…',
  'Now we\u2019re talking…',
  'Curious choice…',
  'Noted…',
  'Filing that away…',
  'Let me chew on this…',
  'Bear with me…',
  'Spicy…',
  'Hold that thought…',
  'Wait, really?',
  'Plot twist…',
  'Cool, cool, cool…',
  'A-ha…',
  'Mm-hmm…',
  'Oh, fascinating…',
  'That tracks…',
  'Bold move…',
  'Okay, that changes things…',
  'Tell me more…',
  'Mulling it over…',
  'Doing the math…',
  'Following a hunch…',
  'Cross-referencing…',
  'Pondering…',
  'Brewing the next one…',
  'Cooking up a question…',
  'Hmm, didn\u2019t see that coming…',
  'Interesting plot point…',
  'You\u2019re keeping me on my toes…',
  'Oh, you\u2019re THAT kind…',
  'Adjusting my read…',
  'Recalculating…',
  'Updating priors…',
  'Reshuffling the deck…',
  'Re-sorting candidates…',
  'Considering the angle…',
  'Wait, that\u2019s a clue…',
  'Counting the tells…',
  'Sketching the next one…',
  'Picking carefully…',
  'Drafting…',
  'Polishing…',
  'Wordsmithing…',
  'Choosing my words…',
  'Refining…',
  'Almost ready…',
  'Just a beat…',
  'One moment…',
  'Composing…',
  'Picking the cleanest angle…',
  'Hmm, that\u2019s telling…',
  'You surprise me…',
  'Now THAT\u2019s a tell…',
  'Got it…',
  'Filing…',
  'Stewing on it…',
  'Steeping a question…',
  'Tweaking the wording…',
  'Adjusting course…',
  'Mapping the next step…',
  'Tilting the question…',
  'Finding the perfect ask…',
  'Splitting hairs…',
  'Weighing trade-offs…',
  'Hmm, intriguing…',
  'Right, right…',
  'Aha, a pattern…',
  'Connecting the dots…',
  'Trying an angle…',
  'Sniffing out a clue…',
  'Listening closely…',
  'Reading the room…',
  'Adjusting the lens…',
  'Sharpening focus…',
  'Sketching options…',
  'Auditioning a question…',
  'Picking my moment…',
  'Closing in…',
  'Zeroing in…',
  'Honing in…',
  'On it…',
  'Cooking…',
  'Whirring…',
  'Thinking out loud…',
  'Running a quick check…',
  'Comparing notes with myself…',
  'Asking the next right question…',
  'Following the thread…',
  'Hmm, fascinating answer…',
  'Spotted a pattern…',
  'Adjusting…',
  'Considering…',
  'Reflecting…',
  'Wondering…',
  'Musing…',
  'Sussing it out…',
  'Connecting some dots…',
  'Picking a thread…',
  'Following through…',
  'Doing my homework…',
  'Triple-checking…',
  'Looking for the punchline…',
  'Finding the angle…',
  'Threading the needle…',
  'Polishing the next question…',
  'Finalizing the next ask…',
  'Almost there…',
  'One more beat…',
  'Ready in a tick…',
  'Coming right up…',
  'Right behind you…',
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

// UX-2026-06-29 (quiz-ux-polish item 1) — a short, progressive "confidence /
// progress" stage hint shown WHILE the agent is actively thinking. It rides
// alongside the playful rotating phrase as a calm grey-italic clause so the
// user always reads "the agent is actively narrowing toward an answer".
//
// Preference order, brief by design (a few words, never a fake percentage):
//   1. Real agent confidence (0-1 or 0-100) → a worded band, NOT a number,
//      because the loading row intentionally hides the numeric "% confident"
//      (that pill is reserved for the idle/awaiting-input state).
//   2. Otherwise derive a tasteful PROGRESSIVE ladder from how far into the
//      quiz we are (question ordinal): early questions read as "gathering
//      more signal", the middle as "narrowing it down", later as
//      "growing confident". This telegraphs momentum without inventing data.
// eslint-disable-next-line react-refresh/only-export-components
export function deriveProgressStage(
  confidence?: number | null,
  questionNumber?: number | null,
): string {
  // 1. Worded confidence band when the agent reports a real value.
  if (typeof confidence === 'number' && Number.isFinite(confidence) && confidence > 0) {
    const pct = Math.min(100, confidence > 1 ? confidence : confidence * 100);
    if (pct >= 75) return 'growing confident';
    if (pct >= 45) return 'narrowing it down';
    return 'gathering more signal';
  }
  // 2. Fall back to a progressive ladder keyed on the question ordinal.
  if (typeof questionNumber === 'number' && Number.isFinite(questionNumber) && questionNumber > 0) {
    if (questionNumber >= 7) return 'growing confident';
    if (questionNumber >= 3) return 'narrowing it down';
    return 'gathering more signal';
  }
  // 3. No signal at all — a calm, honest default.
  return 'gathering more signal';
}

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
  /**
   * AC-UX-2026-05-08 — 0–1 confidence value the agent has in its
   * current best-guess profile. When present and the agent is still
   * thinking we append "(N% confident)" to the visible progress phrase
   * so users see momentum toward a final answer. Omit (or pass null)
   * to keep the phrase clean.
   */
  confidence?: number | null;
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
  confidence,
}: QuestionViewProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  // DRAFT Q&A imagery gate — only render a bound question image when the backend
  // flag is on. Off => exactly today's text-only header (no layout change).
  const { qaImages } = useFeatures();

  useEffect(() => {
    if (question?.id) {
      headingRef.current?.focus();
    }
  }, [question?.id]);

  // Resolve progress fields up-front so the rotation hooks below can be
  // declared unconditionally (lint: react-hooks/rules-of-hooks). All values
  // are safe to compute even when `question` is null.
  const phrase = (progressPhrase ?? question?.progressPhrase ?? '').trim();

  // AC-PROD-R6-FE-ROTATE-1/2 + AC-PROD-R7-TW-POOL-2 + AC-UX-2026-05-25-PART2
  // item 6 — cycle the playful `ACTIVE_THINKING_PHRASES` pool while
  // `isLoading` is true (and no upstream LLM phrase has arrived); use
  // the curated `FINALIZING_PHRASES` pool when the agent has moved into
  // profile-writing mode; fall back to `THINKING_PHRASES` only when no
  // mode hint is provided and we are NOT actively loading (rare).
  const activePool =
    mode === 'finalizing'
      ? FINALIZING_PHRASES
      : isLoading
        ? ACTIVE_THINKING_PHRASES
        : THINKING_PHRASES;
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
  const basePhrase = phrase || (isLoading ? activePool[rotatedIndex] : '');
  // AC-UX-2026-05-08 + AC-UX-2026-05-25-PART2 item 5 — append agent
  // confidence inline so the user can see the quiz progressing toward a
  // high-confidence answer. Per the May 25 review, confidence is now
  // shown ONLY when the agent is IDLE (a question is on screen waiting
  // for input). While the agent is actively thinking we keep the row
  // playful (rotating `ACTIVE_THINKING_PHRASES`) and intentionally hide
  // the numeric score — the score is a between-questions status, not a
  // mid-thought status. Requires (a) phrase non-empty, (b) not loading,
  // (c) numeric confidence in [0,1] or [0,100].
  const confidencePct =
    typeof confidence === 'number' && Number.isFinite(confidence) && confidence > 0
      ? Math.min(100, Math.round((confidence > 1 ? confidence : confidence * 100)))
      : null;
  // AC-UX-2026-05-25-PART3 item 5 — guarantee the top-right thinking
  // row always renders something meaningful. While the agent is busy,
  // fall back to a generic "Thinking\u2026" string if neither the BE
  // phrase nor the rotation pool produced text. When idle, show the
  // confidence pill on its own if no phrase is available so the user
  // always sees "agent presence + status" next to the dots.
  const idleConfidenceLabel =
    !isLoading && confidencePct != null ? `${confidencePct}% confident` : '';
  const displayPhrase = isLoading
    ? basePhrase || 'Thinking\u2026'
    : basePhrase && confidencePct != null
      ? `${basePhrase} (${confidencePct}% confident)`
      : basePhrase || idleConfidenceLabel;

  // UX-2026-06-29 item 1 \u2014 progressive "confidence / progress" stage hint,
  // shown ONLY while the agent is actively thinking. Worded (never a fake
  // percentage), derived from real confidence when present else the question
  // ordinal. Rendered as a separate calm grey-italic clause so the rotating
  // playful phrase stays clean (and existing tests that read the exact pool
  // entry from `quiz-progress-phrase` keep passing).
  const progressStage = isLoading ? deriveProgressStage(confidence, number) : '';

  return (
    <div className="max-w-3xl mx-auto text-center">
      {/* Top status row: agent-status indicator + status phrase, top-right.
          UX REDESIGN (2026-06-29, owner-approved): while the agent is
          working we show a smooth sea-blue (`compliment`) spinner; idle is
          the same-sized quiet static ring (no layout shift). The status
          text is a single calm GREY ITALIC line (the LLM `progress_phrase`
          or local fallback) in the muted token. */}
      <div
        className="mb-5 flex items-center justify-end gap-2 min-h-[1.75rem]"
        data-testid="quiz-thinking-row"
      >
        {/* #19 (HITLIST-2026-06-30) — single live region. The spinner carries a
            STABLE ariaLabel ("Thinking") so its role=status no longer
            re-announces the rotating phrase every 3s; the adjacent aria-live
            span below is the sole announcer of the changing phrase. */}
        <ThinkingIndicator
          thinking={isLoading}
          size="md"
          ariaLabel="Thinking"
        />
        <span
          className="text-sm italic text-muted"
          data-testid="quiz-progress-phrase"
          aria-live="polite"
        >
          {displayPhrase}
        </span>
        {/* Progressive confidence/progress hint — only while the agent is
            actively thinking. Same calm grey-italic styling, separated by a
            faint middot so it reads as a quiet sub-clause, not clutter. It
            always tells the user the agent is working toward an answer
            (gathering more signal → narrowing it down → growing confident). */}
        {progressStage && (
          <span
            className="text-sm italic text-muted/70"
            data-testid="quiz-progress-stage"
            aria-hidden="true"
          >
            · {progressStage}
          </span>
        )}
      </div>

      {/*
        UX-MOTION-2026-06-29 — gentle per-question entrance. Keying the
        wrapper on `question.id` remounts it for each new question, so the
        prompt + answers softly slide-up/fade-in instead of hard-swapping in
        place (smooths the question -> question transition). The `key` on the
        AnswerGrid wrapper likewise re-triggers the staggered tile entrance.
        Decorative only — `.animate-question-in` / `.animate-answer-grid` are
        neutralized under prefers-reduced-motion (see index.css).
      */}
      <div key={question.id} className="animate-question-in">
        {/* DRAFT — same-universe question image (flag-gated). Tiny, lazy,
            decorative, fixed-size slot so it never shifts layout. Renders
            nothing when the flag is off or no safe image is bound. */}
        {qaImages && (
          <QuestionImage
            src={safeImageUrl(question.imageUrl)}
            alt={question.imageAlt || `Illustration for: ${question.text}`}
          />
        )}

        {/* Question text — sized down per UX feedback (was text-2xl/3xl). */}
        <h2
          ref={headingRef}
          tabIndex={-1}
          aria-live="polite"
          className="font-display text-xl sm:text-2xl font-semibold tracking-tight text-fg mb-6 outline-none"
        >
          {question.text}
        </h2>

        {/* Answers (kept: 1 col → 2 cols responsive). The grid itself carries
            the staggered tile-entrance class (see AnswerGrid). */}
        <AnswerGrid
          answers={question.answers}
          onSelect={onSelectAnswer}
          disabled={isLoading}
          selectedId={selectedAnswerId}
        />
      </div>

      {/* Error (if any) */}
      {inlineError && (
        <div className="mt-6" role="alert">
          <p className="text-error mb-3">{inlineError}</p>
          {onRetry && (
            <button
              type="button"
              className="px-4 py-2 rounded-lg bg-fg text-card hover:opacity-90 active:scale-[0.98] transition-[transform,opacity] duration-fast ease-out-token"
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
