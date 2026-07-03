/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { act } from 'react';
import { QuestionView } from './QuestionView';
import {
  THINKING_PHRASES,
  ACTIVE_THINKING_PHRASES,
  FINALIZING_PHRASES,
  deriveProgressStage,
  formatProgressCue,
  formatThinkingFragment,
} from './QuestionView';

const mkQuestion = (overrides: Partial<any> = {}) =>
  ({
    id: 'q1',
    text: 'What is the capital of France?',
    answers: [
      { id: 'a1', text: 'Paris' },
      { id: 'a2', text: 'Berlin' },
      { id: 'a3', text: 'Madrid' },
    ],
    ...overrides,
  } as any);

afterEach(() => cleanup());

describe('QuestionView', () => {
  it('returns null when question is null', () => {
    const { container } = render(
      <QuestionView
        question={null}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders idle status cue, question ordinal, heading and answers', () => {
    const onSelect = vi.fn();

    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={onSelect}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={2}
        progressPhrase="I'm narrowing in…"
      />
    );

    // UX-2026-07-02 v2 — at idle the upper-right shows position + a
    // qualitative closeness tail (not the LLM thinking phrase, not a %).
    // Without a server cap there is no denominator — never a made-up total.
    const pill = screen.getByTestId('quiz-progress-phrase');
    expect(pill.textContent).toBe('Question 2 — getting to know you');
    expect(pill.textContent ?? '').not.toMatch(/%/);

    const ordinal = screen.getByTestId('quiz-question-ordinal');
    expect(ordinal).toHaveTextContent(/^Question 2$/);

    expect(pill.textContent ?? '').not.toMatch(/of up to/);
    expect(screen.queryByText(/%\s*complete/i)).toBeNull();
    expect(screen.queryByRole('progressbar')).toBeNull();

    expect(screen.getByRole('heading', { name: /capital of france/i })).toBeInTheDocument();
    fireEvent.click(screen.getByText('Paris').closest('button') as HTMLButtonElement);
    expect(onSelect).toHaveBeenCalledWith('a1');
  });

  it('derives the idle cue + ordinal from question.questionNumber when props omitted', () => {
    render(
      <QuestionView
        question={mkQuestion({ progressPhrase: 'Still learning…', questionNumber: 7 })}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );

    // questionNumber=7 → "zeroing in" (mid-late). The leftover LLM phrase is
    // deliberately not echoed at idle.
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent('zeroing in');
    expect(screen.getByTestId('quiz-question-ordinal')).toHaveTextContent(/^Question 7$/);
  });

  it('renders the idle ring indicator (and an empty phrase span) when there is no progress signal', () => {
    // AC-PROD-R13-VIS-1 + UX-2026-07-02 — the thinking row ALWAYS renders. In
    // idle with NO ordinal and NO confidence there is genuinely nothing to
    // say, so the phrase span is empty and there is no question-ordinal pill.
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );

    expect(screen.getByTestId('quiz-thinking-row')).toBeInTheDocument();
    expect(screen.getByTestId('thinking-indicator-idle')).toBeInTheDocument();
    expect(screen.queryByTestId('thinking-indicator-spinner')).toBeNull();
    expect(screen.getByTestId('quiz-progress-phrase').textContent ?? '').toBe('');
    expect(screen.queryByTestId('quiz-question-ordinal')).toBeNull();
  });

  it('shows the spinner ThinkingIndicator with an ACTIVE_THINKING_PHRASES fallback while isLoading=true', () => {
    // AC-UX-2026-05-25-PART2 item 6 — while loading the FE rotates the
    // playful ACTIVE_THINKING_PHRASES pool (not the calmer
    // THINKING_PHRASES pool used for idle/fallback).
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
      />
    );
    expect(screen.getByTestId('thinking-indicator-spinner')).toBeInTheDocument();
    const txt = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
    expect(ACTIVE_THINKING_PHRASES).toContain(txt);
  });

  it('uses a single live region for the rotating phrase (#19 — no double-announce)', () => {
    // #19 (HITLIST-2026-06-30) — the thinking-row spinner is role=status, which
    // is itself a live region. Previously its aria-label was the SAME rotating
    // phrase carried by the adjacent aria-live span, so AT announced the phrase
    // twice every 3s. The spinner must now carry a STABLE "Thinking" label, and
    // the aria-live phrase span must be the SOLE announcer of the changing text.
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="I'm narrowing in…"
      />
    );

    const spinner = screen.getByTestId('thinking-indicator-spinner');
    const phraseSpan = screen.getByTestId('quiz-progress-phrase');

    // The spinner's accessible name is the stable "Thinking" — NOT the phrase.
    expect(spinner).toHaveAttribute('aria-label', 'Thinking');
    expect(spinner.getAttribute('aria-label')).not.toContain('narrowing');

    // Only the phrase span is an aria-live region carrying the changing text.
    expect(phraseSpan).toHaveAttribute('aria-live', 'polite');
    expect(phraseSpan).toHaveTextContent("I'm narrowing in");
    // The spinner is not an explicit aria-live region duplicating the phrase
    // (its role=status announces only its own — empty — content, not a copy of
    // the phrase).
    expect(spinner).not.toHaveAttribute('aria-live');
  });

  it('shows the quiet idle ring alongside the qualitative cue when not loading', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={4}
      />
    );
    // Idle state renders the quiet static ring (no spinner) alongside the
    // qualitative closeness cue (questionNumber=4 → "narrowing it down").
    expect(screen.getByTestId('thinking-indicator-idle')).toBeInTheDocument();
    expect(screen.queryByTestId('thinking-indicator-spinner')).toBeNull();
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent('narrowing it down');
  });

  it('focuses the heading when the question mounts and when the question id changes', async () => {
    const { rerender } = render(
      <QuestionView
        question={mkQuestion({ id: 'q1', text: 'Q1 text' })}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={1}
      />
    );

    let heading = screen.getByRole('heading', { name: /q1 text/i });
    await waitFor(() => expect(heading).toHaveFocus());

    rerender(
      <QuestionView
        question={mkQuestion({ id: 'q2', text: 'Q2 text' })}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={2}
      />
    );

    heading = screen.getByRole('heading', { name: /q2 text/i });
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it('disables the AnswerGrid when isLoading is true (buttons disabled)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={true}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={1}
      />
    );

    const buttons = screen.getAllByRole('button');
    // Filter to AnswerGrid buttons (mock question has 3 answers). Other
    // controls outside the grid may exist; only the answer buttons are
    // expected to be disabled while loading.
    const answerButtons = buttons.filter((b) =>
      ['Paris', 'Berlin', 'Madrid'].some((t) => b.textContent?.includes(t)),
    );
    expect(answerButtons.length).toBe(3);
    answerButtons.forEach((b) => expect(b).toBeDisabled());
  });

  it('shows inline error UI and calls onRetry when clicking the retry button', () => {
    const onRetry = vi.fn();

    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError="Network error occurred"
        onRetry={onRetry}
        questionNumber={1}
      />
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/network error occurred/i)).toBeInTheDocument();

    const btn = screen.getByRole('button', { name: /try again/i });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('styles the mid-quiz error with semantic tokens and a primary retry CTA (deep-review #30)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError="Network error occurred"
        onRetry={() => {}}
        questionNumber={1}
      />
    );

    // Standard soft error card — semantic tokens, never red-* literals.
    const alert = screen.getByRole('alert');
    expect(alert.className).toMatch(/bg-error-soft/);
    expect(alert.className).toMatch(/border-error-border/);

    // Retry is the primary button pattern, not the old near-black bg-fg block.
    const btn = screen.getByRole('button', { name: /try again/i });
    expect(btn.className).not.toMatch(/bg-fg/);
    expect(btn.className).toMatch(/min-h-\[44px\]/);
    expect(btn.style.backgroundColor).toContain('var(--color-primary');
  });

  it('passes selectedAnswerId to AnswerGrid (basic presence check via selected answer button)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={1}
        selectedAnswerId="a2"
      />
    );
    expect(screen.getByText('Berlin')).toBeInTheDocument();
  });

  // AC-PROD-R6-FE-ROTATE-1/2 — placeholder rotates while loading and stops
  // the moment a real LLM phrase arrives.
  it('rotates the placeholder phrase while loading with no upstream phrase', () => {
    vi.useFakeTimers();
    try {
      render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
        />
      );
      const first = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
      expect(ACTIVE_THINKING_PHRASES).toContain(first);
      // AC-PROD-R13-ROTATE-1 — rotation interval is 3000ms; advance
      // 3100ms to cross one tick boundary cleanly.
      act(() => {
        vi.advanceTimersByTime(3100);
      });
      const second = screen.getByTestId('quiz-progress-phrase').textContent;
      expect(second).not.toBe(first);
      act(() => {
        vi.advanceTimersByTime(3100);
      });
      const third = screen.getByTestId('quiz-progress-phrase').textContent;
      expect(third).not.toBe(second);
    } finally {
      vi.useRealTimers();
    }
  });

  it('stops rotating once a real progressPhrase arrives', () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
        />
      );
      vi.advanceTimersByTime(3100);
      rerender(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
          progressPhrase="A theme is emerging"
        />
      );
      expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent(
        'A theme is emerging',
      );
      // Further ticks do not change the LLM-supplied phrase.
      act(() => {
        vi.advanceTimersByTime(5200);
      });
      expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent(
        'A theme is emerging',
      );
    } finally {
      vi.useRealTimers();
    }
  });

  // AC-PROD-R7-TW-POOL-1 / AC-PROD-R7-TW-POOL-2
  it('exposes >= 50 unique thinking phrases and a distinct finalizing pool', () => {
    expect(THINKING_PHRASES.length).toBeGreaterThanOrEqual(50);
    expect(new Set(THINKING_PHRASES).size).toBe(THINKING_PHRASES.length);
    expect(FINALIZING_PHRASES.length).toBeGreaterThan(0);
    // Pools must not be identical and must not share every entry.
    const overlap = FINALIZING_PHRASES.filter((p) =>
      THINKING_PHRASES.includes(p),
    );
    expect(overlap.length).toBe(0);
  });

  // AC-PROD-R7-TW-POOL-2 — finalizing mode cycles the profile-writing pool.
  it('cycles the FINALIZING_PHRASES pool when mode="finalizing"', () => {
    vi.useFakeTimers();
    try {
      render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
          mode="finalizing"
        />
      );
      const first = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
      expect(FINALIZING_PHRASES).toContain(first);
      expect(THINKING_PHRASES).not.toContain(first);
    } finally {
      vi.useRealTimers();
    }
  });

  // UX-2026-07-02 (owner feedback) — the status row is ONE quiet message:
  // small, italic, slate-500 (AA 4.76:1) so it never draws the eye from the
  // question. No second clause, no numeric confidence, ever.
  it('renders the status as a single quiet italic line (text-xs, slate-500)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={5}
      />
    );

    const phrase = screen.getByTestId('quiz-progress-phrase');
    expect(phrase.className).toMatch(/\bitalic\b/);
    expect(phrase.className).not.toMatch(/\bnot-italic\b/);
    expect(phrase.className).toMatch(/text-xs/);
    expect(phrase.className).toMatch(/100_116_139/); // slate-500, AA 4.76:1
    // Exactly one status node — the old second "stage" clause is gone.
    expect(screen.queryByTestId('quiz-progress-stage')).toBeNull();
  });

  // UX-2026-07-02 — numeric confidence is NEVER shown ("55% confident"
  // followed by finishing anyway read as broken). The idle status is
  // progress-framed instead: "Question N — <qualitative stage>".
  it('never renders a numeric confidence, idle or thinking', () => {
    const { rerender } = render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={5}
        confidence={0.55}
      />
    );
    expect(screen.getByTestId('quiz-progress-phrase').textContent ?? '').not.toMatch(/%/);

    rerender(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
        questionNumber={5}
        confidence={0.9}
      />
    );
    expect(screen.getByTestId('quiz-progress-phrase').textContent ?? '').not.toMatch(/%/);
  });

  it('frames the idle status as honest position + qualitative tail (owner blackbox v2)', () => {
    // UX-2026-07-02 v2 — the previous "bare stage" cue failed a second
    // blackbox test ("kept saying the same thing"). The single quiet line now
    // carries the real position against the server's topic-aware cap, with
    // "of up to" because the agent can finish early on confidence.
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={5}
        maxQuestions={12}
        confidence={0.5}
      />
    );
    const cue = screen.getByTestId('quiz-progress-phrase');
    expect(cue.textContent).toBe('Question 5 of up to 12 — narrowing it down');
    // Still one message, still no numeric confidence.
    expect(cue.textContent ?? '').not.toMatch(/%/);
    // The bottom ordinal keeps its own (denominator-free) form.
    expect(screen.getByTestId('quiz-question-ordinal')).toHaveTextContent(/^Question 5$/);
  });

  it('reads "almost there" when the agent is genuinely close (high confidence or late quiz)', () => {
    const { rerender } = render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={4}
        confidence={0.85}
      />
    );
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent(/almost there/i);

    rerender(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        questionNumber={11}
      />
    );
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent(/almost there/i);
  });

  it('keeps the thinking state as the single playful phrase when no cap is known', () => {
    // Without the server's maxQuestions there is no honest denominator, so
    // the thinking row stays the bare phrase (no made-up "5/20" fragment).
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
        questionNumber={5}
        progressPhrase="Pondering…"
      />
    );
    const txt = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
    expect(txt).toBe('Pondering…');
    expect(screen.queryByTestId('quiz-progress-stage')).toBeNull();
  });

  // UX-2026-07-02 v2 — thinking dominates wall-clock time, and the owner's
  // blackbox failure was precisely that long thinks showed ONLY playful filler.
  // With the server cap known, the same single line carries a compact position
  // fragment so the user always sees where they stand.
  it('appends the compact position fragment to the thinking phrase (one line)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
        questionNumber={7}
        maxQuestions={12}
        progressPhrase="Pondering…"
      />
    );
    const row = screen.getByTestId('quiz-thinking-row');
    const cue = screen.getByTestId('quiz-progress-phrase');
    expect(cue.textContent).toBe('Pondering… · 7/12');
    // Still exactly ONE status node in the row.
    expect(row.querySelectorAll('[data-testid="quiz-progress-phrase"]').length).toBe(1);
    expect(screen.queryByTestId('quiz-progress-stage')).toBeNull();
  });

  it('carries the position fragment on the rotating placeholder pool too', () => {
    vi.useFakeTimers();
    try {
      render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
          questionNumber={3}
          maxQuestions={12}
        />
      );
      const first = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
      expect(first.endsWith(' · 3/12')).toBe(true);
      expect(ACTIVE_THINKING_PHRASES).toContain(first.slice(0, -' · 3/12'.length));
      // Rotation still works — the playful prefix changes, the fragment stays.
      act(() => {
        vi.advanceTimersByTime(3100);
      });
      const second = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
      expect(second).not.toBe(first);
      expect(second.endsWith(' · 3/12')).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  describe('deriveProgressStage ladder (pure)', () => {
    it('escalates by question ordinal', () => {
      expect(deriveProgressStage(null, 1)).toBe('getting to know you');
      expect(deriveProgressStage(null, 4)).toBe('narrowing it down');
      expect(deriveProgressStage(null, 8)).toBe('zeroing in');
      expect(deriveProgressStage(null, 11)).toBe('almost there');
    });

    it('takes the STRONGER of the confidence band and the ordinal (worded, no %)', () => {
      // UX-2026-07-02 v2 — a present-but-low confidence must never hold the
      // ladder back as answered questions accrue (the exact blackbox failure:
      // the cue "kept saying the same thing" while confidence idled low).
      expect(deriveProgressStage(0.2, 9)).toBe('zeroing in'); // ordinal wins
      expect(deriveProgressStage(0.5, 1)).toBe('narrowing it down'); // confidence wins
      expect(deriveProgressStage(0.65, 1)).toBe('zeroing in'); // new mid band
      expect(deriveProgressStage(0.9, 1)).toBe('almost there');
      // Legacy 0-100 confidence is normalised the same way.
      expect(deriveProgressStage(80, 1)).toBe('almost there');
    });
  });

  // UX-2026-07-02 v2 — GUARANTEE-OF-CHANGE. The owner's blackbox test failed
  // twice because the cue could sit on identical text for long stretches.
  // Walk a full no-confidence quiz (the worst case: the ordinal is the ONLY
  // signal) and pin that the rendered cue keeps moving.
  describe('closeness cue guarantee-of-change (n = 1..12, no confidence)', () => {
    const cueAt = (n: number) =>
      formatProgressCue({ questionNumber: n, maxQuestions: 12, confidence: null });

    it('changes at least every 3 questions and never repeats across the walk', () => {
      const cues = Array.from({ length: 12 }, (_, i) => cueAt(i + 1));

      // Never empty once a question is on screen.
      cues.forEach((c) => expect(c.length).toBeGreaterThan(0));

      // Never repeats anywhere in the walk (the ladder "starts moving" at
      // n=1 since the position is part of the text).
      expect(new Set(cues).size).toBe(cues.length);

      // Changes at least every 3 questions — in fact every question.
      for (let i = 1; i < cues.length; i++) {
        expect(cues[i]).not.toBe(cues[i - 1]);
      }
      for (let i = 3; i < cues.length; i++) {
        expect(new Set(cues.slice(i - 3, i + 1)).size).toBeGreaterThan(1);
      }
    });

    it('escalates the qualitative tail through all four rungs, in order', () => {
      const tails = Array.from({ length: 12 }, (_, i) =>
        cueAt(i + 1).split(' — ')[1],
      );
      const ladder = [
        'getting to know you',
        'narrowing it down',
        'zeroing in',
        'almost there',
      ];
      // Every tail is a known rung and the rank never goes backwards.
      let prevRank = -1;
      for (const t of tails) {
        const rank = ladder.indexOf(t);
        expect(rank).toBeGreaterThanOrEqual(0);
        expect(rank).toBeGreaterThanOrEqual(prevRank);
        prevRank = rank;
      }
      // All four rungs are visited by the end of the walk.
      expect(new Set(tails).size).toBe(4);
      expect(tails[11]).toBe('almost there');
    });

    it('renders the same walk through the component (integration of the wiring)', () => {
      const { rerender } = render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading={false}
          inlineError={null}
          onRetry={() => {}}
          questionNumber={1}
          maxQuestions={12}
        />
      );
      const seen: string[] = [];
      for (let n = 1; n <= 12; n++) {
        rerender(
          <QuestionView
            question={mkQuestion({ id: `q${n}` })}
            onSelectAnswer={() => {}}
            isLoading={false}
            inlineError={null}
            onRetry={() => {}}
            questionNumber={n}
            maxQuestions={12}
          />
        );
        seen.push(screen.getByTestId('quiz-progress-phrase').textContent ?? '');
      }
      expect(new Set(seen).size).toBe(12); // never the same thing twice
      expect(seen[0]).toBe('Question 1 of up to 12 — getting to know you');
      expect(seen[6]).toBe('Question 7 of up to 12 — zeroing in');
      expect(seen[11]).toBe('Question 12 of up to 12 — almost there');
    });
  });

  describe('formatProgressCue / formatThinkingFragment edge cases (pure)', () => {
    it('falls back to the bare stage when only confidence is known', () => {
      expect(formatProgressCue({ confidence: 0.8 })).toBe('almost there');
      expect(formatProgressCue({})).toBe('');
    });

    it('omits the denominator when the cap is unknown (never invents one)', () => {
      expect(formatProgressCue({ questionNumber: 4 })).toBe(
        'Question 4 — narrowing it down',
      );
    });

    it('never renders an impossible fraction (n > cap clamps the denominator)', () => {
      expect(formatProgressCue({ questionNumber: 13, maxQuestions: 12 })).toBe(
        'Question 13 of up to 13 — almost there',
      );
      expect(formatThinkingFragment(13, 12)).toBe(' · 13/13');
    });

    it('requires BOTH numbers for the thinking fragment', () => {
      expect(formatThinkingFragment(7, 12)).toBe(' · 7/12');
      expect(formatThinkingFragment(7, null)).toBe('');
      expect(formatThinkingFragment(null, 12)).toBe('');
      expect(formatThinkingFragment(null, null)).toBe('');
    });
  });

  // UX-MOTION-2026-06-29 — the question prompt + answers are wrapped in a
  // per-question entrance container keyed on question.id so each new question
  // gently slides up/fades in rather than hard-swapping. Decorative motion is
  // neutralized under prefers-reduced-motion (CSS), but the class is always
  // present in the DOM. This guards the wiring so the entrance can't silently
  // regress.
  it('wraps the question body in the per-question entrance container (animate-question-in)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );
    // The heading lives inside the keyed entrance wrapper.
    const heading = screen.getByRole('heading', { name: /capital of france/i });
    const entrance = heading.closest('.animate-question-in');
    expect(entrance).not.toBeNull();
  });
});
