/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { GlobalErrorDisplay } from './GlobalErrorDisplay';

type ApiError = {
  message?: string;
  retriable?: boolean;
  code?: string;
  status?: number;
  details?: unknown;
};

afterEach(() => {
  cleanup();
});

const makeError = (overrides: Partial<ApiError> = {}): ApiError =>
  ({
    message: 'Boom',
    retriable: false,
    ...overrides,
  } as ApiError);

describe('GlobalErrorDisplay', () => {
  it('returns null when error is null', () => {
    const { container } = render(<GlobalErrorDisplay error={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders default (inline) variant with title + message and custom className', () => {
    const err = makeError({ message: 'Something went wrong' });
    render(<GlobalErrorDisplay error={err} className="test-marker-class" />);

    // Container is the <section role="alert">
    const section = screen.getByRole('alert');
    expect(section).toBeInTheDocument();
    expect(section).toHaveClass('test-marker-class');

    // Title defaults
    expect(screen.getByText('An Error Occurred')).toBeInTheDocument();
    // Message from error
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();

    // Inline variant shows the default warning icon (a text span) when no custom icon is provided
    // We can assert the message + structure exists; no retry/start over by default (not retriable, no handler)
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /start over/i })).toBeNull();
  });

  it('renders custom labels and icon (non-page variants)', () => {
    const err = makeError({ message: 'nope', retriable: true });
    const icon = <span data-testid="custom-icon">ICON</span>;

    render(
      <GlobalErrorDisplay
        error={err}
        icon={icon}
        labels={{ title: 'Custom Title', retry: 'Retry Please', startOver: 'Reset' }}
        onRetry={() => {}}
      />
    );

    expect(screen.getByText('Custom Title')).toBeInTheDocument();
    expect(screen.getByTestId('custom-icon')).toBeInTheDocument();
    // Because retriable + onRetry provided
    expect(screen.getByRole('button', { name: 'Retry Please' })).toBeInTheDocument();
    // No start over when retriable
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  });

  it('when retriable=false and onStartOver provided, shows "Start Over" button and calls handler', () => {
    const onStartOver = vi.fn();
    const err = makeError({ retriable: false, message: 'not recoverable' });

    render(<GlobalErrorDisplay error={err} onStartOver={onStartOver} />);

    const btn = screen.getByRole('button', { name: /start over/i });
    expect(btn).toBeInTheDocument();

    fireEvent.click(btn);
    expect(onStartOver).toHaveBeenCalledTimes(1);
  });

  it('when retriable=true and onRetry provided, shows "Try Again" button and calls handler', () => {
    const onRetry = vi.fn();
    const err = makeError({ retriable: true, message: 'retry me' });

    render(<GlobalErrorDisplay error={err} onRetry={onRetry} />);

    const btn = screen.getByRole('button', { name: /try again/i });
    expect(btn).toBeInTheDocument();

    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('autoFocus: focuses the container when error is present', async () => {
    const err = makeError();

    render(<GlobalErrorDisplay error={err} autoFocus />);

    // effect runs after mount
    await act(async () => {});

    const section = screen.getByRole('alert');
    expect(section).toBe(document.activeElement);
    // also verify tabindex was set
    expect(section).toHaveAttribute('tabindex', '-1');
  });

  it('page variant renders the page icon (svg) and applies layout classes', () => {
    const err = makeError({ message: 'bad' });

    const { container } = render(<GlobalErrorDisplay variant="page" error={err} />);

    const section = screen.getByRole('alert');
    expect(section).toBeInTheDocument();
    // Heuristic: page variant uses full-screen layout; ensure the wrapper has an SVG icon
    const svgs = container.querySelectorAll('svg');
    expect(svgs.length).toBeGreaterThan(0);

    // Buttons presence depends on retriable / handlers; with default (not retriable, no handler), none present
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /start over/i })).toBeNull();
  });

  it('message falls back appropriately when error.message is missing', () => {
    // Not retriable -> generic unexpected message
    const err1 = makeError({ message: undefined, retriable: false });
    render(<GlobalErrorDisplay error={err1} />);
    expect(screen.getByText('An unexpected error occurred.')).toBeInTheDocument();
    cleanup();

    // Retriable -> "Please try again."
    const err2 = makeError({ message: undefined, retriable: true });
    render(<GlobalErrorDisplay error={err2} />);
    expect(screen.getByText('Please try again.')).toBeInTheDocument();
  });

  it('title falls back to default when labels.title not provided', () => {
    const err = makeError();
    render(<GlobalErrorDisplay error={err} labels={{}} />);
    expect(screen.getByText('An Error Occurred')).toBeInTheDocument();
  });
});
