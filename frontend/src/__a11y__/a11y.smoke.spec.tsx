// Accessibility smoke tests using axe-core.
// We test small leaf components to avoid pulling in routing/context providers
// for what is fundamentally a markup-quality check.
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { axe, toHaveNoViolations } from 'jest-axe';

import { AnswerTile } from '../components/quiz/AnswerTile';
import { ResultProfile } from '../components/result/ResultProfile';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { HeroCard } from '../components/layout/HeroCard';

expect.extend(toHaveNoViolations);

const baseAnswer = { id: 'a1', text: 'A choice that should be accessible.' } as any;

const baseSynopsis = {
  title: 'A11y Adventure',
  summary: 'A short, accessible synopsis.',
  imageUrl: '/syn.jpg',
  characters: [
    { name: 'Ava', shortDescription: 'Brave hero.', imageUrl: '/a.jpg' },
    { name: 'Bram', shortDescription: 'Wise mentor.', imageUrl: '/b.jpg' },
  ],
} as any;

const baseResult = {
  profileTitle: 'The Calm One',
  summary: 'You bring steadiness.',
  imageUrl: '/r.jpg',
  imageAlt: 'Portrait',
  traits: [
    { id: 't1', label: 'Patient', value: 'High' },
    { id: 't2', label: 'Thoughtful', value: 'Very High' },
  ],
} as any;

describe('a11y smoke (axe-core)', () => {
  it('AnswerTile has no axe violations', async () => {
    const { container } = render(
      <ul>
        <li>
          <AnswerTile answer={baseAnswer} onClick={() => {}} isSelected={false} />
        </li>
      </ul>
    );
    expect(await axe(container)).toHaveNoViolations();
    cleanup();
  });

  it('SynopsisView has no axe violations', async () => {
    const { container } = render(
      <SynopsisView
        synopsis={baseSynopsis}
        characters={undefined}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );
    expect(await axe(container)).toHaveNoViolations();
    cleanup();
  });

  it('ResultProfile has no axe violations', async () => {
    const { container } = render(<ResultProfile result={baseResult} onStartNew={() => {}} />);
    expect(await axe(container)).toHaveNoViolations();
    cleanup();
  });

  it('HeroCard has no axe violations', async () => {
    const { container } = render(
      <HeroCard>
        <h1>Welcome</h1>
        <p>Body content for accessibility scan.</p>
      </HeroCard>
    );
    expect(await axe(container)).toHaveNoViolations();
    cleanup();
  });
});
