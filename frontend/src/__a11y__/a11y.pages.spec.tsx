/**
 * AC-FE-A11Y-PAGES-1..5: Run axe across full-page-equivalent renders to catch
 * accidental nested-landmark / heading-order / color-contrast / label
 * regressions that the per-component smoke tests can miss.
 *
 * We render the canonical states each top-level page emits: question card,
 * loading card, error page, and skip-link/landmark layout.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { render, cleanup } from '@testing-library/react';
import { axe, toHaveNoViolations } from 'jest-axe';

import { QuestionView } from '../components/quiz/QuestionView';
import { LoadingCard } from '../components/loading/LoadingCard';
import { SkipLink } from '../components/common/SkipLink';

vi.mock('/src/components/layout/Header', () => ({
  Header: () => <header>Header</header>,
}));
vi.mock('/src/components/layout/Footer', () => ({
  Footer: () => <footer>Footer</footer>,
}));

expect.extend(toHaveNoViolations);

const baseQuestion = {
  id: 'q1',
  text: 'Which is most accessible?',
  answers: [
    { id: 'a1', text: 'A button with a clear label' },
    { id: 'a2', text: 'A div with an onClick handler' },
    { id: 'a3', text: 'An anchor with no href' },
    { id: 'a4', text: 'A span with role=button' },
  ],
} as any;

describe('a11y page-level scans (FE-A11Y-PAGES)', () => {
  afterEach(() => cleanup());

  it('AC-FE-A11Y-PAGES-1: QuestionView in normal state has no axe violations', async () => {
    const { container } = render(
      <QuestionView
        question={baseQuestion}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError={null}
        onRetry={() => {}}
        progress={{ current: 1, total: 5 }}
        selectedAnswerId={null}
      />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it('AC-FE-A11Y-PAGES-2: QuestionView with inline error preserves a11y semantics', async () => {
    const { container } = render(
      <QuestionView
        question={baseQuestion}
        onSelectAnswer={() => {}}
        isLoading={false}
        inlineError="Something went wrong, please retry."
        onRetry={() => {}}
        progress={{ current: 2, total: 5 }}
        selectedAnswerId={null}
      />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it('AC-FE-A11Y-PAGES-3: LoadingCard region has no axe violations', async () => {
    const { container } = render(<LoadingCard />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('AC-FE-A11Y-PAGES-4: SkipLink in isolation has no axe violations', async () => {
    const { container } = render(
      <>
        <SkipLink />
        <main id="main-content">page</main>
      </>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it('AC-FE-A11Y-PAGES-5: Layout shell (skip link + main + child) has no axe violations and exactly one main', async () => {
    const { Layout } = await import('../components/layout/Layout');
    const { container, queryAllByRole } = render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route
              index
              element={
                <div>
                  <h1>Page heading</h1>
                  <p>Some accessible body content.</p>
                </div>
              }
            />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    // Sanity: only one main landmark.
    expect(queryAllByRole('main').length).toBe(1);
    expect(await axe(container)).toHaveNoViolations();
  });
});
