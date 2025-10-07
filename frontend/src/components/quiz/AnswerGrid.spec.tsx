// src/components/quiz/AnswerGrid.spec.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { AnswerGrid } from './AnswerGrid';

const answers = [
  {
    id: 'a1',
    text: 'Answer One',
    imageUrl: '/1.jpg',
    imageAlt: 'First image',
  },
  {
    id: 'a2',
    text: 'Answer Two',
    imageUrl: '/2.jpg',
    imageAlt: 'Second image',
  },
  {
    id: 'a3',
    text: 'Answer Three',
    imageUrl: '/3.jpg',
    imageAlt: '', // decorative/presentational
  },
] as const;

describe('AnswerGrid', () => {
  afterEach(() => cleanup());

  it('renders a grid of answer tiles', () => {
    const { container } = render(
      <AnswerGrid answers={answers as any} disabled={false} onSelect={() => {}} />
    );

    // Buttons for each answer
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(answers.length);

    // Texts visible
    for (const a of answers) {
      expect(screen.getByText(a.text)).toBeInTheDocument();
    }

    // Images exist with correct attributes.
    // NOTE: the third image has alt="", which is presentational and doesn't expose "img" role,
    // so use a DOM query instead of getByRole.
    const imgs = Array.from(container.querySelectorAll('img')) as HTMLImageElement[];
    expect(imgs).toHaveLength(answers.length);

    const thirdImg = imgs.find((img) => img.src.includes('/3.jpg'));
    expect(thirdImg).toBeTruthy();
    expect(thirdImg).toHaveAttribute('alt', '');
  });

  it('calls onSelect with the clicked answer id when not disabled', () => {
    const onSelect = vi.fn();
    render(<AnswerGrid answers={answers as any} disabled={false} onSelect={onSelect} />);

    const btn = screen.getByText('Answer Two').closest('button') as HTMLButtonElement;
    fireEvent.click(btn);

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('a2');
  });

  it('does not call onSelect when the grid is disabled; buttons are disabled', () => {
    const onSelect = vi.fn();
    render(<AnswerGrid answers={answers as any} disabled={true} onSelect={onSelect} />);

    const allButtons = screen.getAllByRole('button') as HTMLButtonElement[];
    allButtons.forEach((b) => expect(b).toBeDisabled());

    fireEvent.click(allButtons[0]);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('shows the spinner overlay only when the selected tile is disabled', () => {
    const onSelect = vi.fn();

    // Selected + disabled => spinner visible
    const { rerender } = render(
      <AnswerGrid
        answers={answers as any}
        disabled={true}
        onSelect={onSelect}
        selectedId="a1"
      />
    );
    // Spinner within the selected tile (Spinner uses role="status")
    expect(screen.getByRole('status', { name: /loading/i })).toBeInTheDocument();

    // Disabled, but no selection => no spinner
    rerender(
    <AnswerGrid
        answers={answers as any}
        disabled={true}
        onSelect={onSelect}
        selectedId={undefined} // or null
    />
    );
    expect(screen.queryByRole('status', { name: /loading/i })).toBeNull();

    // Enabled, selected => no spinner either
    rerender(
      <AnswerGrid
        answers={answers as any}
        disabled={false}
        onSelect={onSelect}
        selectedId="a1"
      />
    );
    expect(screen.queryByRole('status', { name: /loading/i })).toBeNull();
  });

  it('returns null (renders nothing) when answers is an empty array', () => {
    const { container } = render(<AnswerGrid answers={[]} onSelect={() => {}} />);
    // React Testing Library renders a wrapper div; the component itself renders null
    expect(container.firstChild).toBeNull();
  });
});
