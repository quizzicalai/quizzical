// frontend/src/components/quiz/ThinkingIndicator.spec.tsx
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { ThinkingIndicator } from './ThinkingIndicator';

describe('ThinkingIndicator', () => {
  // AC-PROD-R9-SPINNER-1 — three-dot bouncing spinner (restored from R7).
  it('renders three bouncing dots with role=status when thinking=true', () => {
    const { getByTestId, getByRole, getAllByTestId } = render(
      <ThinkingIndicator thinking />,
    );
    expect(getByTestId('thinking-indicator-spinner')).toBeInTheDocument();
    const dots = getAllByTestId('thinking-indicator-dot');
    expect(dots).toHaveLength(3);
    for (const dot of dots) {
      expect(dot.className).toMatch(/animate-bounce/);
      expect(dot.className).toMatch(/bg-primary/);
    }
    // Spinner row carries role="status"; the circular border-spin
    // primitive is no longer used here.
    const spinner = getByRole('status');
    expect(spinner.className).not.toMatch(/animate-spin/);
  });

  // AC-PROD-R8-GLYPH-1 — primary blue, slightly tilted, sized larger than
  // the spinner row so the punctuation reads as deliberate.
  it('renders the still ∴ glyph in primary colour when thinking=false', () => {
    const { getByTestId, container } = render(
      <ThinkingIndicator thinking={false} />,
    );
    const idle = getByTestId('thinking-indicator-idle');
    expect(idle).toBeInTheDocument();
    expect(idle.textContent).toBe('∴');
    expect(idle.className).toMatch(/text-primary(?!\/)/);
    expect(idle.className).toMatch(/rotate-12/);
    // No spinner row in idle state.
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it('respects custom ariaLabel for the spinner state', () => {
    const { getByLabelText } = render(
      <ThinkingIndicator thinking ariaLabel="Closing in…" />,
    );
    expect(getByLabelText('Closing in…')).toBeInTheDocument();
  });
});
