/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { CONFIG_FIXTURE } from '../../../tests/fixtures/config.fixture';

// ---- Mock useConfig so we can inject different configs ----
let __config: any = null;

vi.mock('/src/context/ConfigContext', () => ({
  __setConfig: (c: any) => (__config = c),
  useConfig: () => ({
    config: __config,
    isLoading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

// Import after mocks
const { __setConfig } = (await import('../../context/ConfigContext')) as any;
const MOD = await import('./InlineError');
const { InlineError } = MOD;

beforeEach(() => {
  cleanup();
  __setConfig(null);
});

describe('InlineError', () => {
  it('renders with labels from config.content.errors when available', () => {
    __setConfig(CONFIG_FIXTURE);

    render(<InlineError message="Something broke" onRetry={vi.fn()} />);

    // Container and heading
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(CONFIG_FIXTURE.content.errors.title)).toBeInTheDocument();

    // Message text
    expect(screen.getByText('Something broke')).toBeInTheDocument();

    // Uses configured retry label
    const retryBtn = screen.getByRole('button', {
      name: CONFIG_FIXTURE.content.errors.retry,
    });
    expect(retryBtn).toBeInTheDocument();
  });

  it('falls back to default labels when config/labels are missing', () => {
    // No config provided -> should use defaults
    __setConfig(null);

    render(<InlineError message="Fallback message" onRetry={vi.fn()} />);

    expect(screen.getByText('Application Error')).toBeInTheDocument();
    // Default retry label
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.getByText('Fallback message')).toBeInTheDocument();
  });

  it('does not render a Retry button when onRetry is not provided', () => {
    __setConfig(CONFIG_FIXTURE);

    render(<InlineError message="No retry here" />);

    // No button at all
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('invokes onRetry when the Retry button is clicked', () => {
    __setConfig(CONFIG_FIXTURE);
    const onRetry = vi.fn();

    render(<InlineError message="Click to retry" onRetry={onRetry} />);

    const retryBtn = screen.getByRole('button', {
      name: CONFIG_FIXTURE.content.errors.retry,
    });
    fireEvent.click(retryBtn);

    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('sets proper accessibility roles/attributes', () => {
    __setConfig(CONFIG_FIXTURE);

    render(<InlineError message="a11y check" onRetry={vi.fn()} />);

    const root = screen.getByRole('alert');
    expect(root).toHaveAttribute('aria-live', 'assertive');
  });
});
