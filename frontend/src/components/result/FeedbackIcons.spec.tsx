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
import { mockApiError } from '../../test-utils/mockApiError';

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

    // Rating chooser behaves as a required radio group for assistive tech.
    const group = screen.getByRole('radiogroup', { name: /was this result helpful\?/i });
    expect(group).toHaveAttribute('aria-required', 'true');
    expect(upBtn).toHaveAttribute('aria-pressed', 'true');
    expect(downBtn).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows visible helper labels under emoji choices', () => {
    render(<FeedbackIcons quizId={quizId} />);
    expect(screen.getByText('Good')).toBeInTheDocument();
    // AC-UX-2026-05-04 — label renamed from "Needs work" to "Poor" so
    // both buttons share a one-word label and the trio reads as a
    // symmetrical control strip.
    expect(screen.getByText('Poor')).toBeInTheDocument();
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

    // AC-UX-2026-05-25-PART2 item 8a — submit requires a non-empty
    // comment, so type one before continuing.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'oops' } });

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

    // AC-UX-2026-05-25-PART2 item 8a — need a non-empty comment.
    fireEvent.change(screen.getByPlaceholderText(/type your thoughts/i), {
      target: { value: 'good stuff' },
    });

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
    // AC-UX-2026-05-25-PART2 item 8a — need a non-empty comment.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'good' } });
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

// §19.4 AC-QUALITY-R2-FE-ERR-2: typed error narrowing using the canonical
// envelope. The component must distinguish RATE_LIMITED / PAYLOAD_TOO_LARGE /
// VALIDATION_ERROR codes and surface user-friendly copy for each.
describe('FeedbackIcons — ApiError envelope handling', () => {
  const quizId = 'quiz-99';

  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  async function submitOnce() {
    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));
    // AC-UX-2026-05-25-PART2 item 8a — submit requires a comment.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'msg' } });
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }));
  }

  it('maps RATE_LIMITED to a wait-and-retry message', async () => {
    (api.submitFeedback as any).mockRejectedValueOnce(
      mockApiError('RATE_LIMITED', { retriable: true, retryAfterMs: 5000 }),
    );
    await submitOnce();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/too many submissions/i);
  });

  it('maps PAYLOAD_TOO_LARGE to a shorten-input message', async () => {
    (api.submitFeedback as any).mockRejectedValueOnce(mockApiError('PAYLOAD_TOO_LARGE'));
    await submitOnce();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/comment is too long/i);
  });

  it('maps VALIDATION_ERROR to a check-input message', async () => {
    (api.submitFeedback as any).mockRejectedValueOnce(mockApiError('VALIDATION_ERROR'));
    await submitOnce();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/check your input/i);
  });

  it('falls back to the generic copy for unknown error codes', async () => {
    (api.submitFeedback as any).mockRejectedValueOnce(mockApiError('SOMETHING_NEW'));
    await submitOnce();
    const alert = await screen.findByRole('alert');
    // Default message bubble (the mocked Error.message includes the code)
    expect(alert).toHaveTextContent(/SOMETHING_NEW|failed to submit feedback/i);
  });
});

// UX audit M9 / M10: visible character counter + submit spinner.
describe('FeedbackIcons — comment counter + submit spinner', () => {
  const quizId = 'quiz-counter';

  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it('shows a live character counter that updates as the user types', () => {
    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));

    const counter = screen.getByTestId('feedback-comment-counter');
    expect(counter).toHaveTextContent('0/4096');

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hello' } });
    expect(screen.getByTestId('feedback-comment-counter')).toHaveTextContent('5/4096');
  });

  it('switches counter to error color past the 80% soft threshold', () => {
    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));

    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'a'.repeat(3300) } });

    const counter = screen.getByTestId('feedback-comment-counter');
    expect(counter.className).toMatch(/text-error/);
  });

  it('renders a spinner inside the submit button while the request is in flight', async () => {
    let resolvePromise: () => void;
    (api.submitFeedback as any).mockImplementation(
      () => new Promise<void>((res) => (resolvePromise = res))
    );

    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));
    // AC-UX-2026-05-25-PART2 item 8a — submit requires a comment.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'msg' } });
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }));

    // Spinner element appears during submission
    expect(screen.getByTestId('feedback-submit-spinner')).toBeInTheDocument();

    // Finish the request and confirm spinner is gone (form replaced by thanks)
    resolvePromise!();
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/thank you/i)
    );
    expect(screen.queryByTestId('feedback-submit-spinner')).toBeNull();
  });

  // AC-UX-2026-05-04 — the two rating buttons must share an identical
  // pre-defined footprint so the trio reads as a symmetrical control
  // strip instead of growing/shrinking with their text labels.
  it('renders both rating buttons with identical fixed-size circular shape', () => {
    render(<FeedbackIcons quizId={quizId} />);
    const up = screen.getByRole('button', { name: /thumbs up/i });
    const down = screen.getByRole('button', { name: /thumbs down/i });

    for (const btn of [up, down]) {
      // Same width and height utilities across both buttons.
      expect(btn.className).toMatch(/\bh-20\b/);
      expect(btn.className).toMatch(/\bw-20\b/);
      // Larger circular target on sm+ for fingers and pointer.
      expect(btn.className).toMatch(/sm:h-24/);
      expect(btn.className).toMatch(/sm:w-24/);
      // Must be circular, not a text-shaped pill.
      expect(btn.className).toMatch(/rounded-full/);
    }
  });

  // AC-UX-2026-05-05 — the submit button is the only way to commit
  // feedback. It must render the literal word "Submit" (not a vague
  // icon) and carry the primary-action background so it reads as a
  // CTA rather than another rating chip.
  it('renders a primary Submit button with visible "Submit" label after a rating is chosen', () => {
    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));

    const submit = screen.getByRole('button', { name: /submit feedback/i });
    // Visible label, not just an aria-label / icon.
    expect(submit.textContent || '').toMatch(/submit/i);
    // Inline style fallback for the primary palette so the button is
    // never invisible even if Tailwind's `bg-primary` regresses.
    const bg = (submit as HTMLButtonElement).style.backgroundColor || '';
    expect(bg.length).toBeGreaterThan(0);
  });
});

// DEEP-REVIEW #20 + #22 — iOS-zoom-safe textarea and single-use Turnstile
// token hygiene on a failed submit (reset + bounded auto-retry).
describe('FeedbackIcons — token hygiene + textarea zoom guard (#20/#22)', () => {
  const quizId = 'quiz-tok';

  beforeEach(() => vi.clearAllMocks());
  afterEach(() => {
    cleanup();
    delete (window as { resetTurnstile?: unknown }).resetTurnstile;
  });

  // #20 — the comment textarea must carry a >=16px font (text-base) so iOS
  // Safari does not auto-zoom the page when it gains focus.
  it('renders the comment textarea with a 16px (text-base) font to avoid iOS auto-zoom', () => {
    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));
    const ta = screen.getByRole('textbox');
    expect(ta.className).toMatch(/\btext-base\b/);
  });

  // #22 — after a failed submit the consumed single-use token must be dropped
  // and a fresh one requested via window.resetTurnstile(), so a retry can't
  // replay the dead token.
  it('resets the Turnstile token after a failed submit (calls resetTurnstile + re-disables submit)', async () => {
    const reset = vi.fn();
    (window as unknown as { resetTurnstile: () => void }).resetTurnstile = reset;
    (api.submitFeedback as any).mockRejectedValueOnce(new Error('Network oops'));

    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'a note' } });
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    const submit = screen.getByRole('button', { name: /submit feedback/i });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    // Error surfaces, the dead token is cleared (submit re-disabled), and a
    // fresh token was requested.
    await screen.findByRole('alert');
    expect(reset).toHaveBeenCalledTimes(1);
    expect(submit).toBeDisabled();
  });

  // #22 — on a token-specific rejection (code: 'turnstile_failed') the
  // component queues exactly one silent auto-retry that fires when the fresh
  // token arrives, so the user isn't forced to click Submit a second time.
  it('auto-retries once when the failure is turnstile_failed and a fresh token arrives', async () => {
    (api.submitFeedback as any)
      .mockRejectedValueOnce(mockApiError('TURNSTILE_INVALID', { code: 'turnstile_failed' }))
      .mockResolvedValueOnce(undefined);

    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'retry me' } });

    // First token → first submit (fails with turnstile_failed).
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    fireEvent.click(screen.getByRole('button', { name: /submit feedback/i }));
    await waitFor(() => expect(api.submitFeedback).toHaveBeenCalledTimes(1));

    // Fresh token arrives → queued auto-retry fires the second submit, which
    // succeeds and shows the thanks state — no manual re-click needed.
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));
    await waitFor(() => expect(api.submitFeedback).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/thank you/i),
    );
  });
});

// AC-UX-2026-05-25-PART2 items 8 + 9 — selection emphasis, submit-gating
// on a non-empty comment, and removal of the stray required-asterisk
// glyph that was reading as a stray character in the prompt copy.
describe('FeedbackIcons — May 25 Part 2 polish', () => {
  const quizId = 'quiz-part2';

  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  // Item 9 — the trailing red asterisk + sr-only "Required" pairing
  // was a holdover required-field marker. The radiogroup already
  // advertises `aria-required` for assistive tech so the visual
  // glyph is unnecessary and reads as garbage next to the prompt.
  it('does not render a trailing required-asterisk glyph in the prompt', () => {
    render(<FeedbackIcons quizId={quizId} />);
    const prompt = screen.getByText(/Was this result helpful\?/i);
    // The prompt paragraph must not end with (or contain) a stray "*".
    expect(prompt.textContent || '').not.toMatch(/\*/);
    // Nor an sr-only "Required" sibling masquerading as form copy.
    expect(screen.queryByText(/^Required$/)).toBeNull();
  });

  // Item 8 (selection emphasis) — the selected rating must wear a
  // thick 4px primary-color border (not a thin black border or a 2px
  // ring) so it is unmistakable. At rest it carries a hairline 2px
  // muted border so the transition is visually obvious.
  it('renders the selected rating with a thick 4px primary-color border', () => {
    render(<FeedbackIcons quizId={quizId} />);
    const up = screen.getByRole('button', { name: /thumbs up/i });
    const down = screen.getByRole('button', { name: /thumbs down/i });

    // Resting state: 2px hairline, no primary emphasis.
    expect(up.className).toMatch(/border-2/);
    expect(up.className).not.toMatch(/border-4/);
    expect(up.className).not.toMatch(/border-primary/);

    fireEvent.click(up);
    expect(up.className).toMatch(/border-4/);
    expect(up.className).toMatch(/border-primary/);
    // Unselected sibling stays on the hairline.
    expect(down.className).not.toMatch(/border-4/);
  });

  // Item 8a — submit must be disabled until the user has typed an
  // actual comment. Rating + Turnstile alone produced low-signal
  // submissions; the comment is the carrier of feedback value.
  it('keeps submit disabled until a non-empty comment is typed', () => {
    render(<FeedbackIcons quizId={quizId} />);
    fireEvent.click(screen.getByRole('button', { name: /thumbs up/i }));
    fireEvent.click(screen.getByRole('button', { name: /mock turnstile verify/i }));

    const submit = screen.getByRole('button', { name: /submit feedback/i });
    // Rating + token but no comment yet → still disabled.
    expect(submit).toBeDisabled();

    // Whitespace-only comments do not count.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '   ' } });
    expect(submit).toBeDisabled();

    // A real comment unlocks the button.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'real feedback' } });
    expect(submit).toBeEnabled();
  });
});
