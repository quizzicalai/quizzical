import React, { useState, useCallback } from 'react';
import * as api from '../../services/apiService';
import clsx from 'clsx';
import type { ApiError } from '../../types/api';
import type { ResultPageConfig } from '../../types/config';
import Turnstile from '../common/Turnstile';

type FeedbackIconsProps = {
  quizId: string;
  labels?: ResultPageConfig['feedback'];
};

// Keep backend values as-is
type Rating = 'up' | 'down';

// Simple emoji map (UI only)
const EMOJI: Record<Rating, string> = {
  up: '😄',   // good
  down: '😕', // bad
};

export function FeedbackIcons({ quizId, labels = {} }: FeedbackIconsProps) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [comment, setComment] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

  const handleChoose = useCallback((newRating: Rating) => {
    if (submitted || isSubmitting) return;
    setRating(newRating);
    setError(null);
  }, [submitted, isSubmitting]);

  const handleSubmit = useCallback(async () => {
    if (!rating || isSubmitting) return;

    if (!turnstileToken) {
      setError(labels?.turnstileError ?? 'Please complete the security check before submitting.');
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      await api.submitFeedback(quizId, { rating, comment }, turnstileToken);
      setSubmitted(true);
    } catch (e: unknown) {
      // §19.4 AC-QUALITY-R2-FE-ERR-2: narrow `unknown` and use the canonical
      // `errorCode` to differentiate user messaging.
      const apiErr = (e ?? {}) as ApiError;
      const code = apiErr.errorCode;
      const fallback = 'Failed to submit feedback. Please try again.';
      const friendly =
        code === 'RATE_LIMITED'
          ? 'Too many submissions. Please wait a moment and try again.'
          : code === 'PAYLOAD_TOO_LARGE'
            ? 'Your comment is too long. Please shorten it.'
            : code === 'VALIDATION_ERROR'
              ? 'Please check your input and try again.'
              : apiErr.message || fallback;
      setError(friendly);
    } finally {
      setIsSubmitting(false);
    }
  }, [quizId, rating, comment, isSubmitting, turnstileToken, labels?.turnstileError]);

  if (submitted) {
    return (
      <div
        data-testid="feedback-icons"
        data-state="submitted"
        className="lp-feedback-card lp-feedback-card--success"
      >
        <p
          className="flex items-center justify-center gap-2 text-center text-success font-medium"
          role="status"
        >
          <span aria-hidden="true" className="text-xl leading-none">✓</span>
          <span>{labels?.thanks ?? 'Thank you, much appreciated!'}</span>
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="feedback-icons"
      data-state={rating ? 'rating-chosen' : 'idle'}
      className="lp-feedback-card space-y-4"
    >
      <p className="font-medium text-center text-fg" id="feedback-rating-label">
        {labels?.prompt ?? 'Was this result helpful?'}
        {/* AC-UX-2026-05-25-PART2 item 9 — the trailing red asterisk
            was a holdover required-field marker that read as a stray
            character next to a binary 👍/👎 radiogroup. The radiogroup
            already advertises `aria-required` for assistive tech so the
            visual indicator is unnecessary and the prompt copy itself
            ("What did you think of your result?") makes the call to
            action obvious. */}
      </p>

      {/* Modern, elegant “ovals” with emojis */}
      <div
        className="flex justify-center gap-5"
        role="radiogroup"
        aria-labelledby="feedback-rating-label"
        aria-required="true"
      >
        {(['up', 'down'] as Rating[]).map((r) => {
          const isActive = rating === r;
          const isPositive = r === 'up';
          return (
            <button
              key={r}
              type="button"
              onClick={() => handleChoose(r)}
              data-testid={`feedback-${r}`}
              aria-pressed={isActive}
              disabled={isSubmitting}
              className={clsx(
                // AC-UX-2026-05-04 — fixed circular size for both
                // buttons so the trio reads as a symmetrical control
                // strip; previously the buttons sized to their label
                // text (\"Good\" vs \"Needs work\"), making the row look
                // accidentally asymmetric.
                'h-20 w-20 sm:h-24 sm:w-24',
                'inline-flex flex-col items-center justify-center rounded-full p-2',
                // AC-UX-2026-05-25-PART2 item 8 — selection is now
                // unmistakable: at rest the circle wears a 2px hairline;
                // when chosen it switches to a 4px primary-color outline
                // and a light primary tint. The previous combo (thin
                // muted border + 2px primary ring + scale-110) read as
                // "slightly highlighted" rather than "definitively
                // selected". The thick border is the dominant signal
                // and survives high-contrast / forced-colors modes.
                'border-2 border-muted/40 bg-card text-fg shadow-sm',
                'hover:bg-bg hover:shadow-md hover:scale-105 active:scale-95',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                'transition-all duration-150 ease-out',
                isActive &&
                  'border-4 border-primary bg-primary/10 text-primary shadow-md scale-110',
                isSubmitting && 'opacity-60 cursor-not-allowed',
                !isSubmitting && 'cursor-pointer',
              )}
              aria-label={r === 'up'
                ? (labels?.thumbsUp ?? 'Thumbs up')
                : (labels?.thumbsDown ?? 'Thumbs down')
              }
            >
              <span className="text-2xl leading-none" aria-hidden="true">{EMOJI[r]}</span>
              <span className="mt-1 text-[11px] font-medium text-muted leading-none">
                {isPositive ? 'Good' : 'Poor'}
              </span>
            </button>
          );
        })}
      </div>

      {rating && (
        <div className="space-y-3 flex flex-col items-center">
          <label htmlFor="feedback-comment" className="sr-only">
            {labels?.commentPlaceholder ?? 'Add a comment'}
          </label>
          <textarea
            id="feedback-comment"
            rows={3}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={labels?.commentPlaceholder ?? 'Add a comment (optional)...'}
            className="w-full p-2 border border-border rounded-md resize-y focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            disabled={isSubmitting}
            maxLength={4096}
            aria-required="false"
            aria-describedby="feedback-comment-counter"
          />
          {/* UX audit M9 / P9: visible char counter (4096 cap) with soft warn at 80%. */}
          <div
            id="feedback-comment-counter"
            data-testid="feedback-comment-counter"
            className={clsx(
              'self-end text-xs tabular-nums',
              comment.length >= 3277 ? 'text-error' : 'text-muted',
            )}
            aria-live="polite"
          >
            {comment.length}/4096
          </div>

          <Turnstile onVerify={setTurnstileToken} />

          <button
            onClick={handleSubmit}
            data-testid="feedback-submit"
            // AC-UX-2026-05-25-PART2 item 8a — require a non-empty
            // comment before enabling Submit. Rating + Turnstile alone
            // produced low-signal submissions ("👍" with no comment),
            // and the visible state of the disabled button now teaches
            // the user that the comment is the carrier of feedback
            // value. `comment.trim()` guards against whitespace-only
            // submissions.
            disabled={isSubmitting || !rating || !turnstileToken || comment.trim().length === 0}
            style={{
              backgroundColor: 'rgb(var(--color-primary, 79 70 229))',
              color: 'rgb(255 255 255)',
            }}
            className="inline-flex w-full items-center justify-center gap-2 px-4 py-2 rounded-lg font-semibold shadow-sm hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 transition-opacity"
          >
            {isSubmitting && (
              <span
                aria-hidden="true"
                data-testid="feedback-submit-spinner"
                className="inline-block h-4 w-4 rounded-full border-2 border-white/40 border-t-white animate-spin"
              />
            )}
            {isSubmitting ? 'Submitting...' : (labels?.submit ?? 'Submit Feedback')}
          </button>
        </div>
      )}
      {error && <p className="text-center text-error" role="alert">{error}</p>}
    </div>
  );
}
