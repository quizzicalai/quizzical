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
        {labels?.prompt ?? 'Was this result helpful?'}{' '}
        <span className="text-error" aria-hidden="true">*</span>
        <span className="sr-only">Required</span>
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
                'h-auto min-h-[48px] min-w-[48px] sm:min-h-[56px] sm:min-w-[56px]',
                'inline-flex flex-col items-center justify-center rounded-full px-2 py-1',
                'border border-muted/40 bg-card text-fg shadow-sm',
                'hover:bg-bg hover:shadow-md hover:scale-110 active:scale-95',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                'transition-all duration-150 ease-out',
                isActive && 'ring-2 ring-primary bg-primary/10 text-primary border-primary/30 scale-110',
                isSubmitting && 'opacity-60 cursor-not-allowed',
                !isSubmitting && 'cursor-pointer',
              )}
              aria-label={r === 'up'
                ? (labels?.thumbsUp ?? 'Thumbs up')
                : (labels?.thumbsDown ?? 'Thumbs down')
              }
            >
              <span className="text-2xl" aria-hidden="true">{EMOJI[r]}</span>
              <span className="mt-0.5 text-[10px] font-medium text-muted leading-none">
                {isPositive ? 'Good' : 'Needs work'}
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
            className="w-full p-2 border rounded-md resize-y focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            disabled={isSubmitting}
            maxLength={4096}
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
            disabled={isSubmitting || !rating || !turnstileToken}
            className="bg-primary inline-flex w-full items-center justify-center gap-2 px-4 py-2 rounded-lg font-semibold text-white shadow-sm hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 transition-opacity"
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
