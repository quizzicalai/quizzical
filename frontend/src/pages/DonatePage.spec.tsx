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

import { DonatePage } from './DonatePage';

describe('DonatePage', () => {
  beforeEach(() => {
    lastStaticPageProps = null;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders StaticPage', () => {
    render(<DonatePage />);
    expect(screen.getByTestId('static-page')).toBeInTheDocument();
  });

  it('passes pageKey="donatePage" to StaticPage', () => {
    render(<DonatePage />);

    const el = screen.getByTestId('static-page');
    expect(el).toHaveAttribute('data-page-key', 'donatePage');
    expect(el).toHaveTextContent(/StaticPage:\s*donatePage/i);

    expect(lastStaticPageProps).toEqual({ pageKey: 'donatePage' });
  });
});
