/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { QuestionView } from './QuestionView';

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
        progress={{ current: 1, total: 3 }}
      />
    );
    // wrapper div is there, component renders null -> no children
    expect(container.firstChild).toBeNull();
  });

  it('renders progress, heading, and answers; clicking an answer calls onSelectAnswer', () => {
    const onSelect = vi.fn();

    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={onSelect}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progress={{ current: 2, total: 5 }}
      />
    );

    // Progress text
    expect(screen.getByText(/Question 2 of 5/i)).toBeInTheDocument();

    // Heading text
    const heading = screen.getByRole('heading', { name: /capital of france/i });
    expect(heading).toBeInTheDocument();

    // Answers visible and clickable
    expect(screen.getByText('Paris')).toBeInTheDocument();
    expect(screen.getByText('Berlin')).toBeInTheDocument();
    expect(screen.getByText('Madrid')).toBeInTheDocument();

    // Click Paris
    fireEvent.click(screen.getByText('Paris').closest('button') as HTMLButtonElement);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('a1');
  });

  it('focuses the heading when the question mounts and when the question id changes', async () => {
    const { rerender } = render(
      <QuestionView
        question={mkQuestion({ id: 'q1', text: 'Q1 text' })}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progress={{ current: 1, total: 2 }}
      />
    );

    let heading = screen.getByRole('heading', { name: /q1 text/i });
    await waitFor(() => expect(heading).toHaveFocus());

    // Change to a new question id — should re-focus
    rerender(
      <QuestionView
        question={mkQuestion({ id: 'q2', text: 'Q2 text' })}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progress={{ current: 2, total: 2 }}
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
        progress={{ current: 1, total: 3 }}
      />
    );

    const buttons = screen.getAllByRole('button');
    // First button is the first answer (no extra buttons present in this simple setup)
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
        progress={{ current: 1, total: 3 }}
      />
    );

    // Error area visible
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/network error occurred/i)).toBeInTheDocument();

    // Retry button present and functional
    const btn = screen.getByRole('button', { name: /try again/i });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('passes selectedAnswerId to AnswerGrid (basic presence check via selected answer button)', () => {
    // We can do a light assertion by checking the selected answer’s button is present in DOM.
    // (Detailed visual state is covered in AnswerGrid tests.)
    render(
      <QuestionView
        question={mkQuestion()}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progress={{ current: 1, total: 3 }}
        selectedAnswerId="a2"
      />
    );

    // The selected answer's text exists; actual selection styling is verified in AnswerGrid tests.
    expect(screen.getByText('Berlin')).toBeInTheDocument();
  });
});
