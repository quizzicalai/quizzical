// frontend/src/components/quiz/ThinkingIndicator.spec.tsx
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { ThinkingIndicator } from './ThinkingIndicator';

describe('ThinkingIndicator', () => {
  // AC-PROD-R8-SPINNER-1 — circular spinner (same primitive as the global
  // quiz-loading spinner) sized to share the bounding box of the idle ∴
  // glyph.
  it('renders a circular primary spinner with role=status when thinking=true', () => {
    const { getByTestId, getByRole, container } = render(
      <ThinkingIndicator thinking />,
    );
    expect(getByTestId('thinking-indicator-spinner')).toBeInTheDocument();
    const status = getByRole('status');
    expect(status).toBeInTheDocument();
    // Inner spinner uses the same animate-spin/border-primary primitive
    // as the global Spinner component.
    const ring = container.querySelector('.animate-spin');
    expect(ring).not.toBeNull();
    expect(ring!.className).toMatch(/border-primary/);
    expect(ring!.className).toMatch(/border-t-transparent/);
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
