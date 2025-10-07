// src/pages/StaticPage.spec.tsx
/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';

// --- Mocks ------------------------------------------------------------------

// Mock the config hook so we can control config per test
vi.mock('../context/ConfigContext', () => {
  return {
    useConfig: vi.fn(),
  };
});

// Mock Spinner to a minimal, accessible element
vi.mock('../components/common/Spinner', () => {
  return {
    Spinner: ({ message }: { message?: string }) => (
      <div role="status">{message ?? 'Loading'}</div>
    ),
  };
});

import { StaticPage } from './StaticPage';
import { useConfig } from '../context/ConfigContext';

describe('StaticPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows Spinner while config is missing', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: undefined });

    render(<StaticPage pageKey="aboutPage" />);

    const status = screen.getByRole('status');
    expect(status).toBeInTheDocument();
    expect(status).toHaveTextContent(/loading/i);
  });

  it('renders fallback "Content Not Available" when page content is missing', () => {
    (useConfig as unknown as Mock).mockReturnValue({
      config: { content: {} },
    });

    render(<StaticPage pageKey="aboutPage" />);

    expect(
      screen.getByRole('heading', { level: 1, name: /content not available/i })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/this page's content could not be loaded\./i)
    ).toBeInTheDocument();
  });

  it('renders title and all supported block types (p, h2, ul, ol) and focuses the title', async () => {
    (useConfig as unknown as Mock).mockReturnValue({
      config: {
        content: {
          aboutPage: {
            title: 'About Us',
            blocks: [
              { type: 'p', text: 'Welcome to our site.' },
              { type: 'h2', text: 'Our Mission' },
              { type: 'ul', items: ['Item A', 'Item B'] },
              { type: 'ol', items: ['Step 1', 'Step 2'] },
            ],
          },
        },
      },
    });

    render(<StaticPage pageKey="aboutPage" />);

    // Title present and focused after mount
    const title = screen.getByRole('heading', { level: 1, name: /about us/i });
    expect(title).toBeInTheDocument();
    await waitFor(() => expect(title).toHaveFocus());

    // Paragraph
    expect(screen.getByText(/welcome to our site\./i)).toBeInTheDocument();

    // Subheading (h2)
    const h2 = screen.getByRole('heading', { level: 2, name: /our mission/i });
    expect(h2).toBeInTheDocument();

    // Unordered list items
    expect(screen.getByText('Item A')).toBeInTheDocument();
    expect(screen.getByText('Item B')).toBeInTheDocument();

    // Ordered list items
    expect(screen.getByText('Step 1')).toBeInTheDocument();
    expect(screen.getByText('Step 2')).toBeInTheDocument();
  });

  it('moves focus to the title when pageKey changes', async () => {
    (useConfig as unknown as Mock).mockReturnValue({
      config: {
        content: {
          aboutPage: {
            title: 'About Us',
            blocks: [{ type: 'p', text: 'About content' }],
          },
          termsPage: {
            title: 'Terms of Use',
            blocks: [{ type: 'p', text: 'Terms content' }],
          },
        },
      },
    });

    const { rerender } = render(<StaticPage pageKey="aboutPage" />);

    const aboutHeading = screen.getByRole('heading', { level: 1, name: /about us/i });
    await waitFor(() => expect(aboutHeading).toHaveFocus());

    // Switch to a different key
    rerender(<StaticPage pageKey="termsPage" />);

    const termsHeading = screen.getByRole('heading', { level: 1, name: /terms of use/i });
    await waitFor(() => expect(termsHeading).toHaveFocus());
  });
});
