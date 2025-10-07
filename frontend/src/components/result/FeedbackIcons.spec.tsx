// src/components/result/FeedbackIcons.spec.tsx
/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';

// --- Mocks ------------------------------------------------------------------

// Mock the API service
vi.mock('../../services/apiService', () => {
  return {
    submitFeedback: vi.fn().mockResolvedValue(undefined),
  };
});

// Mock Turnstile to be a tiny test helper: it renders a button that,
// when clicked, calls onVerify('tok-123').
vi.mock('../common/Turnstile', () => {
  return {
    __esModule: true,
    default: ({ onVerify }: { onVerify: (t: string) => void }) => (
      <div>
        <button
          type="button"
          onClick={() => onVerify('tok-123')}
          aria-label="Mock Turnstile Verify"
        >
          Verify
        </button>
      </div>
    ),
  };
});

import { FeedbackIcons } from './FeedbackIcons';
import * as api from '../../services/apiService';

describe('FeedbackIcons', () => {
  const quizId = 'quiz-42';

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it('renders prompt and rating buttons; textarea and submit appear only after choosing a rating', () => {
    render(<FeedbackIcons quizId={quizId} />);

    // Prompt
    expect(
      screen.getByText(/Was this result helpful\?/i)
    ).toBeInTheDocument();

    // Thumbs up/down buttons
    const upBtn = screen.getByRole('button', { name: /thumbs up/i });
    const downBtn = screen.getByRole('button', { name: /thumbs down/i });
    expect(upBtn).toBeInTheDocument();
    expect(downBtn).toBeInTheDocument();

    // Before choosing: no textarea or submit button visible
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: /submit feedback/i })).toBeNull();

    // Choose "up"
    fireEvent.click(upBtn);
    expect(upBtn).toHaveAttribute('aria-pressed', 'true');
    expect(downBtn).toHaveAttribute('aria-pressed', 'false');

    // After choosing: textarea and submit visible
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: /submit feedback/i });
    expect(submit).toBeInTheDocument();

    // Submit is disabled until Turnstile verification happens
    expect(submit).toBeDisabled();
  });

  it('enables submit only after Turnstile verification; sends payload and shows thanks on success', async () => {
    render(<FeedbackIcons quizId={quizId} />);

    // Choose "down"
    const downBtn = screen.getByRole('button', { name: /thumbs down/i });
    fireEvent.click(downBtn);

    // Add a comment
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'Not helpful for me' } });

    // Submit disabled before token
    const submit = screen.getByRole('button', { name: /submit feedback/i });
    expect(submit).toBeDisabled();

    // Click our mock "Turnstile Verify" button to provide a token
    const verifyBtn = screen.getByRole('button', { name: /mock turnstile verify/i });
    fireEvent.click(verifyBtn);

    // Now submit should be enabled
    expect(submit).toBeEnabled();

    // Click submit
    fireEvent.click(submit);

    // API called with quizId, payload, and token
    await waitFor(() => {
      expect(api.submitFeedback).toHaveBeenCalledTimes(1);
    });

    const call = (api.submitFeedback as any).mock.calls[0];
    expect(call[0]).toBe(quizId);
    expect(call[1]).toEqual({ rating: 'down', comment: 'Not helpful for me' });
    expect(call[2]).toBe('tok-123');

    // Success UI: thanks message replaces form
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent(/thank you/i);
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('shows an error message when the API rejects', async () => {
    (api.submitFeedback as any).mockRejectedValueOnce(new Error('Network oops'));

    render(<FeedbackIcons quizId={quizId} />);

    // Choose "up"
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));

    // Verify to unlock submit
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }));

    // Error alert appears
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/network oops/i);

    // Form remains (not submitted)
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('keeps submit disabled if there is no token, even after choosing rating', () => {
    render(<FeedbackIcons quizId={quizId} />);

    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));

    const submit = screen.getByRole('button', { name: /submit feedback/i });
    expect(submit).toBeDisabled();

    // No API call should be possible
    fireEvent.click(submit);
    expect(api.submitFeedback).not.toHaveBeenCalled();
  });

  it('respects custom labels for a11y and text', async () => {
    render(
      <FeedbackIcons
        quizId={quizId}
        labels={{
          prompt: 'Was this any good?',
          thumbsUp: 'Yes, good',
          thumbsDown: 'No, bad',
          commentPlaceholder: 'Type your thoughts…',
          submit: 'Send',
          thanks: 'Much appreciated!',
          turnstileError: 'Please verify first',
        }}
      />
    );

    expect(screen.getByText(/Was this any good\?/i)).toBeInTheDocument();

    const up = screen.getByRole('button', { name: /yes, good/i });
    const down = screen.getByRole('button', { name: /no, bad/i });
    expect(up).toBeInTheDocument();
    expect(down).toBeInTheDocument();

    fireEvent.click(up);
    expect(screen.getByPlaceholderText(/type your thoughts/i)).toBeInTheDocument();

    // Verify & submit
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() =>
      expect(screen.getByText(/much appreciated/i)).toBeInTheDocument()
    );
  });

  it('prevents multiple rating changes while submitting or after submission', async () => {
    // Make submit hang briefly so we can test "isSubmitting" gating
    let resolvePromise: () => void;
    (api.submitFeedback as any).mockImplementation(
      () => new Promise<void>((res) => (resolvePromise = res))
    );

    render(<FeedbackIcons quizId={quizId} />);

    const up = screen.getByRole('button', { name: /thumbs up/i });
    const down = screen.getByRole('button', { name: /thumbs down/i });

    // Choose "up" and verify
    fireEvent.click(up);
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    // Start submitting
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }));

    // While submitting, trying to change rating should do nothing visually
    fireEvent.click(down);
    expect(up).toHaveAttribute('aria-pressed', 'true');
    expect(down).toHaveAttribute('aria-pressed', 'false');

    // Finish request
    resolvePromise!();
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/thank you/i)
    );

    // After submitted the form is gone; no further interactions available
    expect(screen.queryByRole('button', { name: /thumbs up/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /thumbs down/i })).toBeNull();
  });
});
