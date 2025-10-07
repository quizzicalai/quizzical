/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { ErrorPage } from './ErrorPage';

describe('ErrorPage', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders with default title and message when no props are provided', () => {
    render(<ErrorPage />);

    // Heading (h1) and default message
    const heading = screen.getByRole('heading', { level: 1, name: /something went wrong/i });
    expect(heading).toBeInTheDocument();

    expect(
      screen.getByText(/we're sorry, but an unexpected error occurred\. please try again later\./i)
    ).toBeInTheDocument();

    // No CTA by default
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders custom title and message', () => {
    render(<ErrorPage title="Oops!" message="Custom error message." />);

    expect(screen.getByRole('heading', { level: 1, name: /oops!/i })).toBeInTheDocument();
    expect(screen.getByText(/custom error message\./i)).toBeInTheDocument();
  });

  it('renders a primary CTA button when provided and calls its onClick', () => {
    const onClick = vi.fn();
    render(<ErrorPage primaryCta={{ label: 'Try Again', onClick }} />);

    const button = screen.getByRole('button', { name: /try again/i });
    expect(button).toBeInTheDocument();

    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not render a button when primaryCta is missing', () => {
    render(<ErrorPage title="No CTA" message="Button should not appear." />);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
