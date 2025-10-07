/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary';

// A component that throws during render when `explode` is true
function Boom({ explode = false }: { explode?: boolean }) {
  if (explode) throw new Error('💥 boom');
  return <div data-testid="content">ok</div>;
}

describe('ErrorBoundary', () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    cleanup();
    // Silence React’s and our own boundary error logs
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
    cleanup();
  });

  it('renders children when no error occurs', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );

    expect(screen.getByTestId('content')).toHaveTextContent('ok');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('catches errors from children and renders the default fallback (with error details in test env)', () => {
    render(
      <ErrorBoundary>
        <Boom explode />
      </ErrorBoundary>
    );

    // Default fallback visible
    const fallback = screen.getByRole('alert');
    expect(fallback).toBeInTheDocument();

    // “Error Details” are shown in non-production environments like test
    expect(screen.getByText(/Error Details/i)).toBeInTheDocument();
    // ensure the error message is present (stack or toString)
    expect(screen.getByText(/boom/)).toBeInTheDocument();

    // componentDidCatch logged
    const didLog = consoleErrorSpy.mock.calls.some((args) =>
      String(args[0]).includes('Uncaught error:')
    );
    expect(didLog).toBe(true);
  });

  it('default fallback "Start Over" button calls window.location.assign(origin)', () => {
    // Save and replace the whole location object with a mocked assign
    const originalLocation = window.location;
    const assignSpy = vi.fn();

    Object.defineProperty(window, 'location', {
        value: {
        ...originalLocation,
        assign: assignSpy,
        // (optional) keep fields used by the component
        origin: originalLocation.origin,
        href: originalLocation.href,
        },
        writable: true,
    });

    render(
        <ErrorBoundary>
        <Boom explode />
        </ErrorBoundary>
    );

    fireEvent.click(screen.getByRole('button', { name: /start over/i }));

    expect(assignSpy).toHaveBeenCalledTimes(1);
    expect(assignSpy).toHaveBeenCalledWith(originalLocation.origin);

    // Restore original location
    Object.defineProperty(window, 'location', {
        value: originalLocation,
        writable: true,
    });
    });

  it('renders a custom fallback if provided (and skips default UI)', () => {
    render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">custom</div>}>
        <Boom explode />
      </ErrorBoundary>
    );

    expect(screen.getByTestId('custom-fallback')).toHaveTextContent('custom');
    // Default fallback not present
    expect(screen.queryByRole('button', { name: /start over/i })).toBeNull();
  });

  // NOTE: We intentionally do not assert “production hides details” here because
  // Vite/Vitest inline import.meta.env at build-time, so flipping MODE at runtime
  // in a test won’t affect the compiled branch. That makes such a test flaky.
  // Instead, we cover the visible-details path in test env above.
});
