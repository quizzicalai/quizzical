// src/components/result/BlendedProfileResult.spec.tsx
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { BlendedProfileResult } from './BlendedProfileResult';
import type { ResultProfileData } from '../../types/result';

const blendedResult: ResultProfileData = {
  profileTitle: "You're a D/C blend",
  summary: 'fallback summary',
  resultKind: 'blended_profile',
  profile: {
    primary: 'Dominance',
    secondary: 'Conscientiousness',
    dimensions: [
      { name: 'Dominance', emphasis: 82, blurb: 'You push for results.' },
      { name: 'Conscientiousness', emphasis: 61, blurb: 'You value accuracy.' },
      { name: 'Influence', emphasis: 30, blurb: 'You can rally people.' },
      { name: 'Steadiness', emphasis: 18, blurb: 'You prefer steady change.' },
    ],
    narrative: 'Your answers form a blend led by Dominance.\n\nIt shows up day to day.',
  },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('BlendedProfileResult', () => {
  it('returns null when result is null', () => {
    const { container } = render(<BlendedProfileResult result={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('returns null when there is no profile payload', () => {
    const { container } = render(
      <BlendedProfileResult
        result={{ profileTitle: 'X', summary: 'y', resultKind: 'blended_profile' } as any}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders the title, focuses it, and shows the blend label + summary', async () => {
    render(<BlendedProfileResult result={blendedResult} />);

    const heading = screen.getByRole('heading', { name: /D\/C blend/i });
    expect(heading).toBeInTheDocument();
    await waitFor(() => expect(heading).toHaveFocus());

    expect(screen.getByTestId('blend-label')).toHaveTextContent('D/C blend');
    expect(
      screen.getByText(/Primary: Dominance · Secondary: Conscientiousness/),
    ).toBeInTheDocument();
  });

  it('renders every canonical dimension with an accessible emphasis meter', () => {
    render(<BlendedProfileResult result={blendedResult} />);

    // One meter per dimension, exposing the emphasis to assistive tech.
    const meters = screen.getAllByRole('meter');
    expect(meters).toHaveLength(4);

    const dominanceMeter = screen.getByRole('meter', { name: /Dominance emphasis/i });
    expect(dominanceMeter).toHaveAttribute('aria-valuenow', '82');
    expect(dominanceMeter).toHaveAttribute('aria-valuemin', '0');
    expect(dominanceMeter).toHaveAttribute('aria-valuemax', '100');

    // Per-dimension blurb is shown.
    expect(screen.getByText('You push for results.')).toBeInTheDocument();
    // Primary / secondary are labelled.
    expect(screen.getAllByText('primary').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('secondary').length).toBeGreaterThanOrEqual(1);
  });

  it('renders the blend narrative (not a single-character writeup)', () => {
    render(<BlendedProfileResult result={blendedResult} />);
    const narrative = screen.getByTestId('blended-narrative');
    expect(narrative).toHaveTextContent(/blend led by Dominance/i);
  });

  it('clamps out-of-range emphasis to 0–100', () => {
    const wild: ResultProfileData = {
      ...blendedResult,
      profile: {
        ...blendedResult.profile!,
        secondary: null,
        dimensions: [
          { name: 'Dominance', emphasis: 999, blurb: 'b' },
          { name: 'Influence', emphasis: -5, blurb: 'c' },
        ],
      },
    };
    render(<BlendedProfileResult result={wild} />);
    expect(screen.getByRole('meter', { name: /Dominance emphasis/i })).toHaveAttribute(
      'aria-valuenow',
      '100',
    );
    expect(screen.getByRole('meter', { name: /Influence emphasis/i })).toHaveAttribute(
      'aria-valuenow',
      '0',
    );
  });
});
