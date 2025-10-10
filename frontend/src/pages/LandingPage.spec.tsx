// frontend/src/pages/LandingPage.spec.tsx
/* eslint no-console: ["error", { "allow": ["error", "log"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';

// -------------------- Fixtures --------------------
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// -------------------- Mocks --------------------

// Mock Spinner to a minimal element
vi.mock('../components/common/Spinner', () => ({
  Spinner: ({ message }: { message?: string }) => (
    <div role="status">{message ?? 'Loading'}</div>
  ),
}));

// Turnstile renders a button to simulate verification and another to simulate error.
// IMPORTANT: This mock will only exist in the DOM once the component decides to show Turnstile,
// which happens after the first submit without a token or after an error.
vi.mock('../components/common/Turnstile', () => {
  return {
    __esModule: true,
    default: ({ onVerify, onError }: { onVerify: (t: string) => void; onError?: () => void }) => (
      <div>
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

// Mock the config hook so we can control the returned config
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

  it('renders header (title/subtitle) and an input with placeholder derived from config examples', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    // Title/subtitle from config (case-insensitive match)
    expect(screen.getByText(new RegExp(CONFIG_FIXTURE.content.landingPage.title, 'i'))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(CONFIG_FIXTURE.content.landingPage.subtitle, 'i'))).toBeInTheDocument();

    // Input present with aria-label (from config if provided)
    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });
    expect(input).toBeInTheDocument();

    // Placeholder:
    //  - if landingPage.placeholder provided, use it
    //  - else use first two examples -> e.g., "Ex1", "Ex2"
    const lp = CONFIG_FIXTURE.content.landingPage as any;
    const expectedPlaceholder =
      typeof lp.placeholder === 'string' && lp.placeholder.trim()
        ? lp.placeholder
        : Array.isArray(lp.examples) && lp.examples.length
          ? `e.g., ${lp.examples.slice(0, 2).map((e: string) => `"${e}"`).join(', ')}`
          : `e.g., "Gilmore Girls", "Myers Briggs"`;

    expect(input).toHaveAttribute('placeholder', expectedPlaceholder);
  });

  it('blocks submission if category is blank (button disabled, no inline error yet)', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    const btn = screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }) as HTMLButtonElement;

    // Button should be disabled because input is blank
    expect(btn).toBeDisabled();

    // Clicking does nothing
    fireEvent.click(btn);
    expect(startQuizMock).not.toHaveBeenCalled();

    // No inline error shown yet
    expect(screen.queryByText(/please complete the security verification/i)).toBeNull();
  });

  it('requires Turnstile token: shows inline error when submitting without verification', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });

    fireEvent.change(input, { target: { value: 'Dinosaurs' } });

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    const btn = screen.getByRole('button', { name: new RegExp(submitLabel, 'i') });
    expect(btn).not.toBeDisabled();

    // First submit reveals Turnstile and sets the inline error prompting verification
    fireEvent.click(btn);

    expect(startQuizMock).not.toHaveBeenCalled();
    expect(
      screen.getByText(/please complete the security verification to continue\./i)
    ).toBeInTheDocument();
  });

  it('submits successfully after Turnstile verification, calls startQuiz and navigates', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockResolvedValueOnce(undefined);

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'Chess' },
    });

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    // First submit -> reveals Turnstile
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    // Verify via mock Turnstile (now rendered)
    fireEvent.click(await screen.findByRole('button', { name: /mock turnstile verify/i }));

    // Submit again -> actual startQuiz call
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    expect(startQuizMock).toHaveBeenCalledTimes(1);
    const [argCategory, argToken] = startQuizMock.mock.calls[0];
    expect(argCategory).toBe('Chess');
    expect(argToken).toBe('tok-123');

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/quiz');
    });
  });

  it('handles API error with category_not_found: resets Turnstile, clears token, and shows config error', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockRejectedValueOnce({ code: 'category_not_found' });

    (window as any).resetTurnstile = vi.fn();

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'UnknownSubject' },
    });

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    // Reveal Turnstile
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    // Verify then submit
    fireEvent.click(await screen.findByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    // Error from config shown
    expect(
      await screen.findByText(new RegExp(CONFIG_FIXTURE.content.errors.categoryNotFound, 'i'))
    ).toBeInTheDocument();

    // Reset called
    expect((window as any).resetTurnstile).toHaveBeenCalled();

    // Try submit again without re-verifying -> should prompt token error
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));
    expect(screen.getByText(/please complete the security verification/i)).toBeInTheDocument();
  });

  it('handles generic API error: shows generic error from config', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockRejectedValueOnce(new Error('boom'));

    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    fireEvent.change(screen.getByRole('textbox', { name: new RegExp(aria, 'i') }), {
      target: { value: 'Math' },
    });

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    // Reveal Turnstile
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    // Verify and submit
    fireEvent.click(await screen.findByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    expect(
      await screen.findByText(new RegExp(CONFIG_FIXTURE.content.errors.quizCreationFailed, 'i'))
    ).toBeInTheDocument();
  });

  it('shows a Turnstile error message if the Turnstile onError is triggered', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const aria = CONFIG_FIXTURE.content.landingPage.inputAriaLabel ?? 'Quiz Topic';
    const input = screen.getByRole('textbox', { name: new RegExp(aria, 'i') });
    fireEvent.change(input, { target: { value: 'Topic' } });

    const submitLabel =
      CONFIG_FIXTURE.content.landingPage.submitButton ||
      CONFIG_FIXTURE.content.landingPage.buttonText ||
      'Generate quiz';

    // Reveal Turnstile
    fireEvent.click(screen.getByRole('button', { name: new RegExp(submitLabel, 'i') }));

    // Trigger Turnstile error
    fireEvent.click(await screen.findByRole('button', { name: /mock turnstile error/i }));

    expect(screen.getByText(/verification failed\. please try again\./i)).toBeInTheDocument();
  });
});
