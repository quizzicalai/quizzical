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

  it('renders progress phrase, question ordinal, heading and answers', () => {
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

    const pill = screen.getByTestId('quiz-progress-phrase');
    expect(pill).toHaveTextContent("I'm narrowing in");

    const ordinal = screen.getByTestId('quiz-question-ordinal');
    expect(ordinal).toHaveTextContent(/^Question 2$/);

    expect(screen.queryByText(/of\s*\d+/i)).toBeNull();
    expect(screen.queryByText(/%\s*complete/i)).toBeNull();
    expect(screen.queryByRole('progressbar')).toBeNull();

    expect(screen.getByRole('heading', { name: /capital of france/i })).toBeInTheDocument();
    fireEvent.click(screen.getByText('Paris').closest('button') as HTMLButtonElement);
    expect(onSelect).toHaveBeenCalledWith('a1');
  });

  it('falls back to question.progressPhrase / questionNumber when props are omitted', () => {
    render(
      <QuestionView
        question={mkQuestion({ progressPhrase: 'Still learning…', questionNumber: 7 })}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );

    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent('Still learning');
    expect(screen.getByTestId('quiz-question-ordinal')).toHaveTextContent(/^Question 7$/);
  });

  it('renders the idle ring indicator (and an empty phrase span) when no phrase + not loading', () => {
    // AC-PROD-R13-VIS-1 — the thinking row ALWAYS renders. In idle the
    // indicator is a quiet static ring, the phrase span is empty, and
    // there is no question-ordinal pill (that pill is independent).
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

  it('shows the quiet idle ring alongside the LLM phrase when not loading', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="Closing in"
      />
    );
    // Idle state renders the quiet static ring (no spinner) alongside the
    // LLM-supplied phrase.
    expect(screen.getByTestId('thinking-indicator-idle')).toBeInTheDocument();
    expect(screen.queryByTestId('thinking-indicator-spinner')).toBeNull();
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent('Closing in');
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

  // UX REDESIGN (2026-06-29, owner-approved) — the status phrase now reads
  // as a single calm GREY ITALIC line in the muted token (text-sm italic
  // text-muted), pairing with the sea-blue spinner when active.
  it('renders the progress phrase as a calm grey italic line', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="Narrowing in…"
      />
    );

    const phrase = screen.getByTestId('quiz-progress-phrase');
    expect(phrase.className).toMatch(/\bitalic\b/);
    expect(phrase.className).not.toMatch(/\bnot-italic\b/);
    // A11y (2026-07-01): migrated off the failing text-muted (slate-400 ~2.6:1)
    // to the AA secondary-text token (slate-600, 7.58:1).
    expect(phrase.className).not.toMatch(/text-muted/);
    expect(phrase.className).toMatch(/--color-text-secondary/);
    expect(phrase.className).toMatch(/text-sm/);
  });

  // AC-UX-2026-05-08 — surface agent confidence at the end of the
  // thinking phrase so users see the model getting more certain. We
  // accept either 0-1 floats or legacy 0-100 percentages from the
  // backend; both must render as "(N% confident)".
  it('appends the agent confidence as "(N% confident)" when the agent is idle', () => {
    // AC-UX-2026-05-25-PART2 item 5 — confidence is now shown ONLY when
    // the agent is NOT thinking (a question is on screen awaiting input).
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="Getting closer"
        confidence={0.85}
      />
    );
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent(
      /Getting closer \(85% confident\)/i,
    );
  });

  it('normalises a legacy 0-100 confidence value to a percent (idle state)', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="Getting closer"
        confidence={72}
      />
    );
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent(
      /Getting closer \(72% confident\)/i,
    );
  });

  it('does not show the confidence suffix when confidence is null/undefined', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="Getting closer"
        confidence={null}
      />
    );
    const txt = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
    expect(txt).not.toMatch(/% confident/);
  });

  it('hides the confidence suffix while the agent is actively thinking', () => {
    // AC-UX-2026-05-25-PART2 item 5 — the loading row stays playful;
    // numeric confidence is reserved for the idle/awaiting-input state.
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading
        inlineError={null}
        onRetry={() => {}}
        progressPhrase="Getting closer"
        confidence={0.9}
      />
    );
    const txt = screen.getByTestId('quiz-progress-phrase').textContent ?? '';
    expect(txt).not.toMatch(/% confident/);
  });

  // UX-2026-06-29 (quiz-ux-polish item 1) — while the agent is actively
  // thinking, a short PROGRESSIVE "confidence/progress" stage hint rides
  // alongside the playful rotating phrase as a calm grey-italic clause so
  // the user always reads "the agent is actively narrowing toward an
  // answer". It is worded (never a fake percentage) and derived from real
  // confidence when present, else the question ordinal.
  describe('progressive stage hint (item 1)', () => {
    it('shows a worded progress stage clause ONLY while loading, in grey italic', () => {
      render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
          questionNumber={1}
        />
      );
      const stage = screen.getByTestId('quiz-progress-stage');
      expect(stage).toBeInTheDocument();
      // Worded, not a fake percentage.
      expect(stage.textContent ?? '').toMatch(/gathering more signal/i);
      expect(stage.textContent ?? '').not.toMatch(/%/);
      // Calm grey-italic styling (matches the phrase styling).
      expect(stage.className).toMatch(/\bitalic\b/);
      expect(stage.className).toMatch(/text-muted/);
    });

    it('does not render the stage clause while idle (awaiting input)', () => {
      render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading={false}
          inlineError={null}
          onRetry={() => {}}
          questionNumber={5}
          progressPhrase="Closing in"
        />
      );
      expect(screen.queryByTestId('quiz-progress-stage')).toBeNull();
    });

    it('escalates the worded stage as the quiz progresses (ordinal fallback)', () => {
      // deriveProgressStage is the pure source of truth for the ladder.
      expect(deriveProgressStage(null, 1)).toBe('gathering more signal');
      expect(deriveProgressStage(null, 4)).toBe('narrowing it down');
      expect(deriveProgressStage(null, 8)).toBe('growing confident');
    });

    it('prefers a real confidence band over the ordinal fallback (worded, no %)', () => {
      expect(deriveProgressStage(0.2, 9)).toBe('gathering more signal');
      expect(deriveProgressStage(0.5, 1)).toBe('narrowing it down');
      expect(deriveProgressStage(0.9, 1)).toBe('growing confident');
      // Legacy 0-100 confidence is normalised the same way.
      expect(deriveProgressStage(80, 1)).toBe('growing confident');
    });

    it('renders a high-confidence stage clause while finalizing', () => {
      render(
        <QuestionView
          question={mkQuestion()}
          onSelectAnswer={() => {}}
          isLoading
          inlineError={null}
          onRetry={() => {}}
          questionNumber={8}
          mode="finalizing"
        />
      );
      expect(screen.getByTestId('quiz-progress-stage').textContent ?? '').toMatch(
        /growing confident/i,
      );
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
