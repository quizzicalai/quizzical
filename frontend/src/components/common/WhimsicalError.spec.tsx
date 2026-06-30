import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { WhimsicalError } from './WhimsicalError';

describe('WhimsicalError', () => {
  afterEach(() => cleanup());

  it('renders the whimsical message', () => {
    render(
      <WhimsicalError message="The muses are briefly unreachable 🎭 — try again in a moment." />,
    );
    expect(
      screen.getByTestId('whimsical-error-message').textContent,
    ).toContain('muses are briefly unreachable');
  });

  it('renders the QF code as light-grey small text below the message', () => {
    render(
      <WhimsicalError
        message="The muses are briefly unreachable 🎭."
        code="QF-LLM-PROVIDER-DOWN"
      />,
    );
    const codeEl = screen.getByTestId('whimsical-error-code');
    expect(codeEl.textContent).toContain('QF-LLM-PROVIDER-DOWN');
    // Light-grey = the secondary/muted token, quieted via opacity. Small font.
    expect(codeEl.style.color).toContain('--color-text-secondary');
    expect(codeEl.style.opacity).toBe('0.7');
    expect(codeEl.className).toContain('text-xs');
  });

  it('shows the trace id alongside the code for support correlation', () => {
    render(
      <WhimsicalError
        message="Something tangled."
        code="QF-UNKNOWN"
        traceId="abc-123"
      />,
    );
    const codeEl = screen.getByTestId('whimsical-error-code');
    expect(codeEl.textContent).toContain('QF-UNKNOWN');
    expect(codeEl.textContent).toContain('ref abc-123');
  });

  it('omits the code line entirely when no code or trace id is given', () => {
    render(<WhimsicalError message="No code here." />);
    expect(screen.queryByTestId('whimsical-error-code')).toBeNull();
  });

  it('renders and fires the primary CTA', () => {
    const onClick = vi.fn();
    render(
      <WhimsicalError
        message="Try again."
        code="QF-UNKNOWN"
        primaryCta={{ label: 'Start Over', onClick }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Start Over' }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('uses role=alert for assistive tech', () => {
    render(<WhimsicalError message="x" code="QF-UNKNOWN" />);
    expect(screen.getByTestId('whimsical-error')).toHaveAttribute('role', 'alert');
  });

  it('does NOT leak raw technical detail (renders only what it is given)', () => {
    // The component is purely presentational — it shows the message it is
    // given, so the whimsical (non-technical) copy is what the user sees.
    render(
      <WhimsicalError
        message="Our quiz-brain wandered off chasing a thought 🧠 — give it another go."
        code="QF-AGENT-TIMEOUT"
      />,
    );
    const msg = screen.getByTestId('whimsical-error-message').textContent ?? '';
    expect(msg.toLowerCase()).not.toContain('traceback');
    expect(msg.toLowerCase()).not.toContain('timeout error');
  });
});
