/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// --- Mock StaticPage so we can inspect the props it receives ---
let lastStaticPageProps: any = null;

vi.mock('./StaticPage', () => {
  const StaticPageMock = ({ pageKey }: { pageKey: string }) => {
    lastStaticPageProps = { pageKey };
    return (
      <div data-testid="static-page" data-page-key={pageKey}>
        StaticPage: {pageKey}
      </div>
    );
  };
  return { StaticPage: StaticPageMock };
});

import { AboutPage } from './AboutPage';

describe('AboutPage', () => {
  beforeEach(() => {
    lastStaticPageProps = null;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders StaticPage', () => {
    render(<AboutPage />);
    expect(screen.getByTestId('static-page')).toBeInTheDocument();
  });

  it('passes pageKey="aboutPage" to StaticPage', () => {
    render(<AboutPage />);

    // Assert via rendered output
    const el = screen.getByTestId('static-page');
    expect(el).toHaveAttribute('data-page-key', 'aboutPage');
    expect(el).toHaveTextContent(/StaticPage:\s*aboutPage/i);

    // Assert via captured props from the mock
    expect(lastStaticPageProps).toEqual({ pageKey: 'aboutPage' });
  });
});
