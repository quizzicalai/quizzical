// src/components/common/Spinner.spec.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Spinner } from './Spinner';

afterEach(() => cleanup());

describe('Spinner', () => {
  it('renders with default size (md) and accessibility attributes', () => {
    const { getByRole } = render(<Spinner />);

    const spinner = getByRole('status'); // result-scoped
    expect(spinner).toHaveAttribute('aria-label', 'Loading');
    expect(spinner).toHaveClass(
      'animate-spin',
      'rounded-full',
      'border-primary',
      'border-t-transparent',
      'w-8',
      'h-8',
      'border-4'
    );

    const container = spinner.parentElement as HTMLElement;
    expect(container).toHaveClass('flex', 'flex-col', 'items-center', 'justify-center', 'gap-4', 'p-6');

    // no message by default
    expect(screen.queryByText(/.+/)).not.toBeInTheDocument();
  });

  it('merges custom className onto the container', () => {
    const { getByRole } = render(<Spinner className="custom-class another" />);
    const spinner = getByRole('status');
    const container = spinner.parentElement as HTMLElement;
    expect(container).toHaveClass('custom-class', 'another');
  });

  it('renders the optional message when provided', () => {
    render(<Spinner message="Loading Configuration..." />);
    expect(screen.getByText('Loading Configuration...')).toBeInTheDocument();
  });

  it('uses sm size when size="sm"', () => {
    const { getByRole } = render(<Spinner size="sm" />);
    const spinner = getByRole('status');
    expect(spinner).toHaveClass('w-4', 'h-4', 'border-2');
  });

  it('uses lg size when size="lg"', () => {
    const { getByRole } = render(<Spinner size="lg" />);
    const spinner = getByRole('status');
    expect(spinner).toHaveClass('w-12', 'h-12', 'border-8');
  });

  it('falls back to md for unknown size keys', () => {
    // @ts-expect-error intentional incorrect size
    const { getByRole } = render(<Spinner size="xl" />);
    const spinner = getByRole('status');
    expect(spinner).toHaveClass('w-8', 'h-8', 'border-4'); // md
  });
});
