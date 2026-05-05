/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { act } from 'react';
import { QuestionView } from './QuestionView';
import { THINKING_PHRASES, FINALIZING_PHRASES } from './QuestionView';

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

  it('renders no pill / no ordinal when neither prop nor question carries them', () => {
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
      />
    );

    expect(screen.queryByTestId('quiz-progress-phrase')).toBeNull();
    expect(screen.queryByTestId('quiz-question-ordinal')).toBeNull();
  });

  it('shows the spinner ThinkingIndicator with a "Thinking…" fallback while isLoading=true', () => {
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
    expect(screen.getByTestId('quiz-progress-phrase')).toHaveTextContent('Thinking…');
  });

  it('shows the still two-dot indicator alongside the LLM phrase when not loading', () => {
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
    // AC-PROD-R13-DOTS-1 — idle state renders the two static dots
    // (no rotation, no glyph) alongside the LLM-supplied phrase.
    expect(screen.getByTestId('thinking-indicator-idle')).toBeInTheDocument();
    expect(screen.getByTestId('thinking-indicator-dot-dark')).toBeInTheDocument();
    expect(screen.getByTestId('thinking-indicator-dot-light')).toBeInTheDocument();
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
    buttons.forEach((b) => expect(b).toBeDisabled());
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
      const first = screen.getByTestId('quiz-progress-phrase').textContent;
      expect(first).toBe('Thinking…');
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
});
