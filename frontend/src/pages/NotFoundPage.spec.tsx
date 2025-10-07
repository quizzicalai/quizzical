/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Mock the config hook so we can control the returned config per test
vi.mock('../context/ConfigContext', () => ({
  useConfig: vi.fn(),
}));

import NotFoundPage from './NotFoundPage';
import { useConfig } from '../context/ConfigContext';

describe('NotFoundPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  const renderWithRouter = () =>
    render(
      <MemoryRouter initialEntries={['/missing']}>
        <NotFoundPage />
      </MemoryRouter>
    );

  it('renders fallback content when config is missing', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: undefined });

    renderWithRouter();

    // Big 404 header
    expect(screen.getByRole('heading', { level: 1, name: /404/i })).toBeInTheDocument();

    // Fallback heading & subheading
    expect(
      screen.getByRole('heading', { level: 2, name: /page not found/i })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/sorry, we couldn't find the page you're looking for\./i)
    ).toBeInTheDocument();

    // Fallback button text and link to "/"
    const link = screen.getByRole('link', { name: /go back home/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/');
  });

  it('uses custom copy from config when provided', () => {
    (useConfig as unknown as Mock).mockReturnValue({
      config: {
        content: {
          notFoundPage: {
            heading: 'Nothing to see here',
            subheading: 'The page you requested does not exist.',
            buttonText: 'Take me home',
          },
        },
      },
    });

    renderWithRouter();

    // Still has the 404 display
    expect(screen.getByRole('heading', { level: 1, name: /404/i })).toBeInTheDocument();

    // Custom heading & subheading
    expect(
      screen.getByRole('heading', { level: 2, name: /nothing to see here/i })
    ).toBeInTheDocument();
    expect(screen.getByText(/the page you requested does not exist\./i)).toBeInTheDocument();

    // Custom button text and correct href
    const link = screen.getByRole('link', { name: /take me home/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/');
  });
});
