/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

// Force the Q&A imagery flag OFF for this whole module — the SUT must then
// render exactly today's text-only behaviour (no bound image, Logo fallback).
vi.mock('../../context/ConfigContext', () => ({
  useFeatures: () => ({ turnstile: true, turnstileEnabled: true, qaImages: false }),
}));

vi.mock('../../assets/icons/Logo', () => ({
  Logo: (props: any) => <svg data-testid="logo-fallback" {...props} />,
}));

import { AnswerTile } from './AnswerTile';
import { QuestionImage } from './QuestionImage';

afterEach(() => cleanup());

describe('Q&A imagery flag gate (OFF)', () => {
  it('AnswerTile ignores imageUrl and shows the Logo fallback when the flag is off', () => {
    render(
      <AnswerTile
        answer={{ id: 'a1', text: 'Brave', imageUrl: '/img1.jpg', imageAlt: 'Alt' } as any}
        onClick={vi.fn()}
      />,
    );
    // No image rendered even though imageUrl is present.
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByTestId('logo-fallback')).toBeInTheDocument();
  });
});

describe('QuestionImage', () => {
  it('renders nothing when src is null', () => {
    const { container } = render(<QuestionImage src={null} alt="x" />);
    expect(container.firstChild).toBeNull();
  });

  it('renders a lazy decorative image when given a safe src', () => {
    render(<QuestionImage src="/q.jpg" alt="A scene" />);
    const img = screen.getByRole('img') as HTMLImageElement;
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(img).toHaveAttribute('decoding', 'async');
    expect(img).toHaveAttribute('width', '128');
    expect(img).toHaveAttribute('height', '128');
    expect(img).toHaveAttribute('alt', 'A scene');
  });

  it('fails open to nothing on image error (no cross-origin placeholder)', () => {
    render(<QuestionImage src="/bad.jpg" alt="x" />);
    fireEvent.error(screen.getByRole('img'));
    expect(screen.queryByRole('img')).toBeNull();
  });
});
