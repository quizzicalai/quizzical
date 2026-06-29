/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// --- Controllable config mock (DonatePage reads content?.donationUrl) ---
let __cfg: any = { content: {} };
vi.mock('../context/ConfigContext', () => ({
  useConfig: () => ({ config: __cfg }),
}));

// --- Mock StaticPage so we can inspect the props it receives + render children ---
let lastStaticPageProps: any = null;

vi.mock('./StaticPage', () => {
  const StaticPageMock = ({
    pageKey,
    children,
  }: {
    pageKey: string;
    children?: React.ReactNode;
  }) => {
    lastStaticPageProps = { pageKey };
    return (
      <div data-testid="static-page" data-page-key={pageKey}>
        StaticPage: {pageKey}
        {children}
      </div>
    );
  };
  return { StaticPage: StaticPageMock };
});

import { DonatePage } from './DonatePage';

describe('DonatePage', () => {
  beforeEach(() => {
    lastStaticPageProps = null;
    __cfg = { content: {} };
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

  it('renders a Ko-fi support button when donationUrl is configured', () => {
    __cfg = { content: { donationUrl: 'https://ko-fi.com/quafel' } };
    render(<DonatePage />);
    const go = screen.getByTestId('donate-page-go');
    expect(go).toHaveAttribute('href', 'https://ko-fi.com/quafel');
    expect(go).toHaveAttribute('target', '_blank');
  });

  it('renders no donate button when donationUrl is empty (degrades to text only)', () => {
    __cfg = { content: { donationUrl: '' } };
    render(<DonatePage />);
    expect(screen.queryByTestId('donate-page-go')).toBeNull();
  });
});
