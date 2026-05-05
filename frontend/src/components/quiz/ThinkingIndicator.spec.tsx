// frontend/src/components/quiz/ThinkingIndicator.spec.tsx
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { ThinkingIndicator } from './ThinkingIndicator';

describe('ThinkingIndicator', () => {
  it('renders an animated spinner with role=status when thinking=true', () => {
    const { getByTestId, getByRole } = render(<ThinkingIndicator thinking />);
    expect(getByTestId('thinking-indicator-spinner')).toBeInTheDocument();
    const spinner = getByRole('status');
    expect(spinner.className).toMatch(/animate-spin/);
  });

  it('renders the still ∴ glyph when thinking=false', () => {
    const { getByTestId, container } = render(
      <ThinkingIndicator thinking={false} />,
    );
    const idle = getByTestId('thinking-indicator-idle');
    expect(idle).toBeInTheDocument();
    expect(idle.textContent).toBe('∴');
    // No spinner should be present in the idle state.
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it('respects custom ariaLabel for the spinner state', () => {
    const { getByLabelText } = render(
      <ThinkingIndicator thinking ariaLabel="Closing in…" />,
    );
    expect(getByLabelText('Closing in…')).toBeInTheDocument();
  });
});
