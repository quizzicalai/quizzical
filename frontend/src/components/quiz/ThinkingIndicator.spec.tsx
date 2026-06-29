// frontend/src/components/quiz/ThinkingIndicator.spec.tsx
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, within } from '@testing-library/react';
import { ThinkingIndicator } from './ThinkingIndicator';

afterEach(cleanup);

describe('ThinkingIndicator', () => {
  // UX REDESIGN (2026-06-29) — idle state is a quiet, static ring. No
  // rotation, no role=status, no bright leading arc.
  it('renders a quiet static ring when thinking=false', () => {
    const { container } = render(<ThinkingIndicator thinking={false} />);
    const scope = within(container);
    const idle = scope.getByTestId('thinking-indicator-idle');
    expect(idle).toBeInTheDocument();
    expect(idle.className).not.toMatch(/animate-spin/);
    // Sea-blue compliment accent.
    expect(idle.className).toMatch(/text-compliment/);
    // No leading arc <path> in the idle state — only the faint track circle.
    expect(container.querySelector('path')).toBeNull();
    expect(container.querySelector('circle')).not.toBeNull();

    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  // UX REDESIGN (2026-06-29) — active state is a smooth spinner in the
  // sea-blue `compliment` accent (animate-spin), exposing role=status.
  it('renders a smooth compliment-colored spinner when thinking=true', () => {
    const { container } = render(<ThinkingIndicator thinking />);
    const scope = within(container);
    const spinner = scope.getByTestId('thinking-indicator-spinner');
    expect(spinner).toBeInTheDocument();
    expect(spinner.className).toMatch(/animate-spin/);
    expect(spinner.className).toMatch(/text-compliment/);
    // Active state draws the bright leading arc on top of the faint track.
    expect(container.querySelector('path')).not.toBeNull();

    expect(scope.getByRole('status')).toBe(spinner);
  });

  it('keeps the same bounding box class across idle and thinking (no reflow)', () => {
    const idle = render(<ThinkingIndicator thinking={false} />);
    const idleBox = idle
      .getByTestId('thinking-indicator-idle')
      .className.match(/w-\d+/)?.[0];
    cleanup();
    const active = render(<ThinkingIndicator thinking />);
    const activeBox = active
      .getByTestId('thinking-indicator-spinner')
      .className.match(/w-\d+/)?.[0];
    expect(idleBox).toBeTruthy();
    expect(idleBox).toBe(activeBox);
  });

  it('respects custom ariaLabel for the spinner state', () => {
    const { container } = render(
      <ThinkingIndicator thinking ariaLabel="Closing in…" />,
    );
    expect(within(container).getByLabelText('Closing in…')).toBeInTheDocument();
  });
});
