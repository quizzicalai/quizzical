// frontend/src/pages/LandingPage.spec.tsx
/* eslint no-console: ["error", { "allow": ["error", "log"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';

// -------------------- Fixtures --------------------
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// -------------------- Mocks --------------------

// Minimal Spinner
vi.mock('../components/common/Spinner', () => ({
  Spinner: ({ message }: { message?: string }) => (
    <div role="status">{message ?? 'Loading'}</div>
  ),
}));

/**
 * Turnstile mock:
 * - Always rendered (the real component is "invisible" but present in DOM).
 * - Exposes two buttons to simulate success + error callbacks.
 */
vi.mock('../components/common/Turnstile', () => {
  return {
    __esModule: true,
    default: ({ onVerify, onError }: { onVerify: (t: string) => void; onError?: () => void }) => (
      <div data-testid="turnstile-mock">
        <button type="button" onClick={() => onVerify('tok-123')} aria-label="Mock Turnstile Verify">
          Verify
        </button>
        <button type="button" onClick={() => onError?.()} aria-label="Mock Turnstile Error">
          Error
        </button>
      </div>
    ),
  };
});

// Mock the config hook so we can return a stable config
vi.mock('../context/ConfigContext', () => ({
  useConfig: vi.fn(),
}));

// Mock quiz store
const startQuizMock = vi.fn();
vi.mock('../store/quizStore', () => ({
  useQuizActions: () => ({ startQuiz: startQuizMock }),
}));

// Mock navigation
const navigateMock = vi.fn();
vi.mock('react-router-dom', async (orig) => {
  const actual = await (orig() as any);
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

import { useConfig } from '../context/ConfigContext';
import { LandingPage } from './LandingPage';

describe('LandingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows a Spinner while config is missing', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: undefined });

    render(<LandingPage />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent(/loading/i);
  });

  it('renders subtitle, the "Which __ am I?" question frame, and input placeholder derived from config', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    // The title string is intentionally NOT rendered as a visible heading;
    // the question composition is the visual hero.
    expect(screen.queryByRole('heading', { level: 1 })).toBeNull();

    // Subtitle still rendered from config
    expect(
      screen.getByText(new RegExp(CONFIG_FIXTURE.content.landingPage.subtitle, 'i'))
    ).toBeInTheDocument();

    // Question frame surrounds the input with "Which … am I?"
    const frame = screen.getByTestId('lp-question-frame');
    expect(frame).toHaveTextContent(/which/i);
    expect(frame).toHaveTextContent(/am i\?/i);

    // Input and placeholder
    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });
    expect(input).toBeInTheDocument();

    const lp = CONFIG_FIXTURE.content.landingPage as any;
    // Placeholder is now driven by a rotating pool of personality-quiz prompts.
    // It must be a non-empty string; the configured value is only used as a
    // fallback while the input is busy.
    const placeholderAttr = input.getAttribute('placeholder');
    expect(typeof placeholderAttr).toBe('string');
    expect((placeholderAttr ?? '').length).toBeGreaterThan(0);
    void lp;
  });

  it('does not submit when category is blank (no token/error rendered)', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    const btn = screen.getByRole('button', { name: new RegExp(submitLabel, 'i') });
    // Clicking with blank input should not submit
    fireEvent.click(btn);
    expect(startQuizMock).not.toHaveBeenCalled();
    // No inline error about Turnstile; we only show plain errors
    expect(screen.queryByText(/please complete the security verification/i)).toBeNull();
  });

  it('prevents submit until both category and token exist; allows single-click submit after Verify', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';
    const btn = screen.getByRole('button', { name: new RegExp(submitLabel, 'i') });

    // Enter category but do NOT verify → click does nothing
    fireEvent.change(input, { target: { value: 'Dinosaurs' } });
    fireEvent.click(btn);
    expect(startQuizMock).not.toHaveBeenCalled();

    // Verify → now click should submit exactly once
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(btn);
    expect(startQuizMock).toHaveBeenCalledTimes(1);
    const [argCategory, argToken] = startQuizMock.mock.calls[0];
    expect(argCategory).toBe('Dinosaurs');
    expect(argToken).toBe('tok-123');
  });

  it('submits successfully after verification and navigates to /quiz', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockResolvedValueOnce(undefined);

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'Chess' },
    });

    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';
    const btn = screen.getByRole('button', { name: new RegExp(submitLabel, 'i') });

    fireEvent.click(btn);

    expect(startQuizMock).toHaveBeenCalledTimes(1);
    const [argCategory, argToken] = startQuizMock.mock.calls[0];
    expect(argCategory).toBe('Chess');
    expect(argToken).toBe('tok-123');

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/quiz');
    });
  });

  it('on category_not_found: shows config error, resets Turnstile, clears token, and blocks further submits until re-verify', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockRejectedValueOnce({ code: 'category_not_found' });

    // Our LandingPage calls window.resetTurnstile after a backend error
    (window as any).resetTurnstile = vi.fn();

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'UnknownSubject' },
    });

    // Verify → first submit → backend error
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';
    const btn = screen.getByRole('button', { name: new RegExp(submitLabel, 'i') });
    fireEvent.click(btn);

    // Error visible
    expect(
      await screen.findByText(new RegExp(CONFIG_FIXTURE.content.errors.categoryNotFound, 'i'))
    ).toBeInTheDocument();

    // Turnstile reset requested
    expect((window as any).resetTurnstile).toHaveBeenCalled();

    // Try to submit again WITHOUT re-verify → should NOT call startQuiz a second time
    fireEvent.click(btn);
    await waitFor(() => expect(startQuizMock).toHaveBeenCalledTimes(1));
  });

  it('on generic API error: shows generic config error message', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockRejectedValueOnce(new Error('boom'));

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'Math' },
    });

    // Verify + submit
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    // Generic error from config
    expect(
      await screen.findByText(new RegExp(CONFIG_FIXTURE.content.errors.quizCreationFailed, 'i'))
    ).toBeInTheDocument();
  });

  it('shows a Turnstile error message when onError is triggered and prevents submit until a new Verify', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'Topic' },
    });

    // Simulate Turnstile error
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile error/i }));
    expect(screen.getByText(/verification failed\. please try again\./i)).toBeInTheDocument();

    // Clicking submit now should NOT call startQuiz (no token)
    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));
    expect(startQuizMock).not.toHaveBeenCalled();

    // After verifying, it should allow submission
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));
    expect(startQuizMock).toHaveBeenCalledTimes(1);
  });

  it('renders a diverse suggested-topic explorer and populates input when a chip is clicked', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    const chips = screen.getAllByTestId('topic-suggestion-chip');
    expect(chips.length).toBeGreaterThanOrEqual(8);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') }) as HTMLInputElement;

    fireEvent.click(chips[0]);
    expect(input.value.trim().length).toBeGreaterThan(0);
  });

  it('renders a chip cloud beneath the input without instructional or shuffle affordances', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    expect(screen.getAllByTestId('topic-suggestion-chip').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /shuffle/i })).toBeNull();
    expect(screen.queryByText(/need inspiration/i)).toBeNull();
    expect(screen.queryByText(/no signup required/i)).toBeNull();
  });

  it('shows a clear-topic affordance only when input has text and keeps focus after clearing', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') }) as HTMLInputElement;

    expect(screen.queryByRole('button', { name: /clear topic/i })).toBeNull();

    fireEvent.change(input, { target: { value: 'Ancient Rome' } });
    const clearBtn = screen.getByRole('button', { name: /clear topic/i });
    expect(clearBtn).toBeInTheDocument();

    fireEvent.click(clearBtn);
    expect(input.value).toBe('');
    await waitFor(() => expect(input).toHaveFocus());
  });

  it('renders inline errors with alert semantics', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockRejectedValueOnce(new Error('boom'));

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'Math' },
    });
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(new RegExp(CONFIG_FIXTURE.content.errors.quizCreationFailed, 'i'));

    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });
    expect(input).toHaveAttribute('aria-describedby', expect.stringContaining('landing-topic-error'));
  });
});
