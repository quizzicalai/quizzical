// frontend/src/pages/LandingPage.spec.tsx
/* eslint no-console: ["error", { "allow": ["error", "log"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

// -------------------- Fixtures --------------------
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// -------------------- Mocks --------------------

// Mock Spinner to a minimal element
vi.mock('../components/common/Spinner', () => ({
  Spinner: ({ message }: { message?: string }) => (
    <div role="status">{message ?? 'Loading'}</div>
  ),
}));

// Mock Logo to a simple span
vi.mock('../assets/icons/Logo', () => ({
  Logo: (props: any) => <span data-testid="logo" {...props} />,
}));

// We’ll capture InputGroup’s latest props so we can assert prop pass-through
let lastInputGroupProps: any;
vi.mock('../components/common/InputGroup', () => {
  const InputGroup = (props: any) => {
    lastInputGroupProps = props;
    const {
      value,
      onChange,
      ariaLabel,
      placeholder,
      errorText,
      isSubmitting,
      formId,
      buttonText,
    } = props;
    return (
      <div>
        {errorText ? <div role="alert">{errorText}</div> : null}
        <input
          aria-label={ariaLabel}
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange((e.target as HTMLInputElement).value)}
        />
        {/* submit button associates with the form via form attribute */}
        <button type="submit" form={formId} disabled={isSubmitting}>
          {buttonText}
        </button>
      </div>
    );
  };
  return { InputGroup };
});

// Turnstile renders a button to simulate verification and another to simulate error
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
    lastInputGroupProps = undefined;
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

  it('renders header (title/subtitle) and wires InputGroup with placeholder and aria-label', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });

    render(<LandingPage />);

    // Title/subtitle from config
    expect(screen.getByText(/unlock your inner persona/i)).toBeInTheDocument();
    expect(screen.getByText(/answer a few questions/i)).toBeInTheDocument();

    // Input rendered via our mock
    const input = screen.getByRole('textbox', { name: /quiz category input/i });
    expect(input).toBeInTheDocument();

    // Placeholder should use landingPage.examples[0] if present, else inputPlaceholder
    expect(input).toHaveAttribute('placeholder', CONFIG_FIXTURE.content.landingPage.examples[0]);

    // Ensure props were passed down
    expect(lastInputGroupProps.minLength).toBe(CONFIG_FIXTURE.limits.validation.category_min_length);
    expect(lastInputGroupProps.maxLength).toBe(CONFIG_FIXTURE.limits.validation.category_max_length);
    expect(lastInputGroupProps.validationMessages).toEqual({
      minLength: CONFIG_FIXTURE.content.landingPage.validation.minLength,
      maxLength: CONFIG_FIXTURE.content.landingPage.validation.maxLength,
      patternMismatch: undefined,
    });
  });

  it('blocks submission if category is blank (no token error not shown until there is content)', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    // Submit with empty input
    fireEvent.click(screen.getByRole('button', { name: /create my quiz/i }));
    expect(startQuizMock).not.toHaveBeenCalled();
    // The component returns early, no inline error yet (token error appears only when trying with non-empty category)
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('requires Turnstile token: shows inline error when submitting without verification', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    const input = screen.getByRole('textbox', { name: /quiz category input/i });
    fireEvent.change(input, { target: { value: 'Dinosaurs' } });

    fireEvent.click(screen.getByRole('button', { name: /create my quiz/i }));

    expect(startQuizMock).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/please complete the security verification/i);
  });

  it('submits successfully after Turnstile verification, calls startQuiz and navigates', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockResolvedValueOnce(undefined);

    render(<LandingPage />);

    // Enter category
    fireEvent.change(screen.getByRole('textbox', { name: /quiz category input/i }), {
      target: { value: 'Chess' },
    });

    // Verify via mock Turnstile
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /create my quiz/i }));

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
    // Make startQuiz reject with code
    startQuizMock.mockRejectedValueOnce({ code: 'category_not_found' });

    // Provide resetTurnstile global
    (window as any).resetTurnstile = vi.fn();

    render(<LandingPage />);

    fireEvent.change(screen.getByRole('textbox', { name: /quiz category input/i }), {
      target: { value: 'UnknownSubject' },
    });
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: /create my quiz/i }));

    // Error from config shown
    expect(
      await screen.findByText(new RegExp(CONFIG_FIXTURE.content.errors.categoryNotFound, 'i'))
    ).toBeInTheDocument();

    // Reset called
    expect((window as any).resetTurnstile).toHaveBeenCalled();

    // Try submit again without re-verifying -> should prompt token error
    fireEvent.click(screen.getByRole('button', { name: /create my quiz/i }));
    expect(
      screen.getByRole('alert')
    ).toHaveTextContent(/please complete the security verification/i);
  });

  it('handles generic API error: shows generic error from config', async () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    startQuizMock.mockRejectedValueOnce(new Error('boom'));

    render(<LandingPage />);

    fireEvent.change(screen.getByRole('textbox', { name: /quiz category input/i }), {
      target: { value: 'Math' },
    });
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: /create my quiz/i }));

    expect(
      await screen.findByText(new RegExp(CONFIG_FIXTURE.content.errors.quizCreationFailed, 'i'))
    ).toBeInTheDocument();
  });

  it('shows a Turnstile error message if the Turnstile onError is triggered', () => {
    (useConfig as unknown as Mock).mockReturnValue({ config: CONFIG_FIXTURE });
    render(<LandingPage />);

    fireEvent.click(screen.getByRole('button', { name: /mock turnstile error/i }));
    expect(screen.getByText(/verification failed\. please try again\./i)).toBeInTheDocument();
  });
});
    async function waitFor<T>(
        callback: () => T | Promise<T>,
        { timeout = 2000, interval = 50 } = {}
    ): Promise<T> {
        const start = Date.now();

        return new Promise<T>((resolve, reject) => {
            const attempt = async () => {
                try {
                    const result = await callback();
                    resolve(result);
                } catch (err) {
                    if (Date.now() - start >= timeout) {
                        reject(err);
                    } else {
                        setTimeout(attempt, interval);
                    }
                }
            };
            attempt();
        });
    }
