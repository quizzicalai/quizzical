// src/components/quiz/AnswerGrid.spec.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { AnswerGrid } from './AnswerGrid';

// Blackbox fix #6 — the AnswerTile's Logo placeholder was REMOVED; the empty
// state renders a clean text-only tile (no <img>, no placeholder element), so
// there is no Logo to mock/detect any more.

// The AnswerTile only renders the bound answer image when the backend Q&A
// imagery flag is ON. These tests exercise that image path, so force the flag
// on (mirrors AnswerTile.spec.tsx). With no provider the default is OFF, which
// would (correctly) render the Logo fallback instead of an <img>.
vi.mock('../../context/ConfigContext', () => ({
  useFeatures: () => ({ turnstile: true, turnstileEnabled: true, qaImages: true }),
}));

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

    // Images exist with correct attributes. The shipped AnswerTile generates
    // a descriptive alt ("Image for: <text>") when imageAlt is missing/empty,
    // so the third tile (imageAlt: '') gets a generated alt rather than ''.
    const imgs = Array.from(container.querySelectorAll('img')) as HTMLImageElement[];
    expect(imgs).toHaveLength(answers.length);

    const thirdImg = imgs.find((img) => img.src.includes('/3.jpg'));
    expect(thirdImg).toBeTruthy();
    expect(thirdImg).toHaveAttribute('alt', 'Image for: Answer Three');
  });

  // #12 (HITLIST-2026-06-30) — the grid renders the shipped AnswerTile, which
  // provides a fixed-height image box with a skeleton pulse while loading and a
  // Logo broken-image fallback. The prior inline tile had none, so a late/null
  // FAL image left a blank gap (CLS) / empty box.
  it('renders the shipped AnswerTile with a loading skeleton, then collapses to text-only on image error', () => {
    const onSelect = vi.fn();
    render(
      <AnswerGrid
        answers={[{ id: 'a1', text: 'Answer One', imageUrl: '/1.jpg', imageAlt: 'First' }] as any}
        disabled={false}
        onSelect={onSelect}
      />
    );

    // Skeleton pulse is present while the image is still loading.
    expect(document.querySelector('.animate-pulse')).toBeInTheDocument();

    // Simulate the image failing (FAL answer images often arrive late/null).
    const img = screen.getByRole('img');
    fireEvent.error(img);

    // Blackbox #6 — NO placeholder: the <img> is gone, no skeleton lingers, and
    // the tile collapses to a clean text-only tile.
    expect(screen.queryByRole('img')).toBeNull();
    expect(document.querySelector('.animate-pulse')).toBeNull();
    expect(screen.getByText('Answer One')).toBeInTheDocument();
  });

  it('renders a clean text-only tile (no placeholder) when an answer has no imageUrl', () => {
    render(
      <AnswerGrid
        answers={[{ id: 'a1', text: 'Answer One' }] as any}
        disabled={false}
        onSelect={() => {}}
      />
    );
    // Blackbox #6 — no <img>, no placeholder, just the answer text.
    expect(screen.queryByRole('img')).toBeNull();
    expect(document.querySelector('.animate-pulse')).toBeNull();
    expect(screen.getByText('Answer One')).toBeInTheDocument();
  });

  // Owner rule (2026-07-01): answer images are ALL-OR-NONE. When only some
  // answers in a set have an image, the grid shows NO images (never a ragged
  // grid where some tiles have an image and others don't).
  it('renders NO images when only some answers have an image (all-or-none)', () => {
    const mixed = [
      { id: 'a1', text: 'Answer One', imageUrl: '/1.jpg', imageAlt: 'First' },
      { id: 'a2', text: 'Answer Two' }, // no image
      { id: 'a3', text: 'Answer Three', imageUrl: '/3.jpg', imageAlt: '' },
    ];
    const { container } = render(<AnswerGrid answers={mixed as any} onSelect={() => {}} />);
    expect(container.querySelectorAll('img')).toHaveLength(0);
    // Tiles collapse to clean text-only — text is still present, no placeholder.
    expect(screen.getByText('Answer One')).toBeInTheDocument();
    expect(screen.getByText('Answer Two')).toBeInTheDocument();
  });

  // UX-MOTION-2026-06-29 — the grid carries the `animate-answer-grid` class so
  // its direct children (the per-answer wrappers) get a subtle staggered
  // entrance. Decorative motion is neutralized under prefers-reduced-motion in
  // CSS; this guards the class wiring against silent regression.
  it('applies the staggered tile-entrance class to the grid container', () => {
    const { container } = render(
      <AnswerGrid answers={answers as any} disabled={false} onSelect={() => {}} />
    );
    const grid = container.querySelector('.animate-answer-grid');
    expect(grid).not.toBeNull();
    // The stagger targets the grid's direct children (one per answer).
    expect(grid?.children.length).toBe(answers.length);
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

  it('never renders an in-tile spinner overlay over the selected answer (AC-UX-2026-05-25-PART3 item 5)', () => {
    const onSelect = vi.fn();

    // The previous design overlaid a Spinner (role="status") on the
    // selected tile while the agent was thinking. UX feedback was that
    // this competed with the top-right ThinkingIndicator and made the
    // selection feel "stuck". The overlay was removed; busy state is
    // now communicated by aria-busy on the tile + the ThinkingIndicator
    // in the header. This test pins that no in-tile spinner ever
    // appears, regardless of selected/disabled combination.

    // Selected + disabled (the formerly-spinning case)
    const { rerender } = render(
      <AnswerGrid
        answers={answers as any}
        disabled={true}
        onSelect={onSelect}
        selectedId="a1"
      />
    );
    expect(screen.queryByRole('status', { name: /loading/i })).toBeNull();
    // aria-busy still communicates the in-flight selection to AT.
    const selectedBtn = screen
      .getByLabelText(/answer one/i)
      .closest('button') as HTMLButtonElement;
    expect(selectedBtn).toHaveAttribute('aria-busy', 'true');

    // Disabled, no selection => still no spinner
    rerender(
      <AnswerGrid
        answers={answers as any}
        disabled={true}
        onSelect={onSelect}
        selectedId={undefined}
      />
    );
    expect(screen.queryByRole('status', { name: /loading/i })).toBeNull();

    // Enabled, selected => still no spinner
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
