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

  it('renders title/subtitle and input placeholder derived from config', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    // Title/subtitle from config
    expect(
      screen.getByText(new RegExp(CONFIG_FIXTURE.content.landingPage.title, 'i'))
    ).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(CONFIG_FIXTURE.content.landingPage.subtitle, 'i'))
    ).toBeInTheDocument();

    // Input and placeholder
    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });
    expect(input).toBeInTheDocument();

    const lp = CONFIG_FIXTURE.content.landingPage as any;
    const expectedPlaceholder =
      typeof lp.placeholder === 'string' && lp.placeholder.trim()
        ? lp.placeholder
        : Array.isArray(lp.examples) && lp.examples.length
          ? `e.g., ${lp.examples.slice(0, 2).map((e: string) => `"${e}"`).join(', ')}`
          : `e.g., "Gilmore Girls", "Myers Briggs"`;

    expect(input).toHaveAttribute('placeholder', expectedPlaceholder);
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
});
