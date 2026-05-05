// frontend/src/components/quiz/ThinkingIndicator.spec.tsx
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, within } from '@testing-library/react';
import { ThinkingIndicator } from './ThinkingIndicator';

afterEach(cleanup);

describe('ThinkingIndicator', () => {
  // AC-PROD-R13-DOTS-1 — idle state shows the same two dots as the
  // spinner, just not rotating. Dark dot is bg-primary; light dot is
  // bg-primary/50 and one Tailwind step smaller.
  it('renders two static dots when thinking=false', () => {
    const { container } = render(<ThinkingIndicator thinking={false} />);
    const scope = within(container);
    const idle = scope.getByTestId('thinking-indicator-idle');
    expect(idle).toBeInTheDocument();
    expect(idle.className).not.toMatch(/animate-spin/);

    const dark = scope.getByTestId('thinking-indicator-dot-dark');
    const light = scope.getByTestId('thinking-indicator-dot-light');
    expect(dark.className).toMatch(/bg-primary(?!\/)/);
    expect(light.className).toMatch(/bg-primary\/50/);
    expect(dark.className).toMatch(/w-2(?!\.)/);
    expect(light.className).toMatch(/w-1\.5/);
    expect(light.className).toMatch(/top-0/);
    expect(light.className).toMatch(/right-0/);
    expect(dark.className).toMatch(/bottom-0/);
    expect(dark.className).toMatch(/left-0/);

    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  // AC-PROD-R13-DOTS-2 — thinking state renders the SAME two dots
  // inside a rotating container (the dots "just started spinning").
  it('renders the same two dots inside an animate-spin container when thinking=true', () => {
    const { container } = render(<ThinkingIndicator thinking />);
    const scope = within(container);
    const spinner = scope.getByTestId('thinking-indicator-spinner');
    expect(spinner).toBeInTheDocument();
    expect(spinner.className).toMatch(/animate-spin/);

    const dark = scope.getByTestId('thinking-indicator-dot-dark');
    const light = scope.getByTestId('thinking-indicator-dot-light');
    expect(dark.className).toMatch(/bg-primary(?!\/)/);
    expect(light.className).toMatch(/bg-primary\/50/);

    expect(scope.getByRole('status')).toBe(spinner);
  });

  it('respects custom ariaLabel for the spinner state', () => {
    const { container } = render(
      <ThinkingIndicator thinking ariaLabel="Closing in…" />,
    );
    expect(within(container).getByLabelText('Closing in…')).toBeInTheDocument();
  });
});
