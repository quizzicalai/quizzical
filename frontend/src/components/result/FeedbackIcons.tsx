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
      <p className="font-medium text-center text-fg">
        {labels?.prompt ?? 'Was this result helpful?'}
      </p>

      {/* Modern, elegant “ovals” with emojis */}
      <div className="flex justify-center gap-5">
        {(['up', 'down'] as Rating[]).map((r) => {
          const isActive = rating === r;
          return (
            <button
              key={r}
              type="button"
              onClick={() => handleChoose(r)}
              data-testid={`feedback-${r}`}
              aria-pressed={isActive}
              disabled={isSubmitting}
              className={clsx(
                'h-12 w-12 sm:h-14 sm:w-14',
                'inline-flex items-center justify-center rounded-full',
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
            className="w-full p-2 border rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            disabled={isSubmitting}
            maxLength={4096}
          />

          <Turnstile onVerify={setTurnstileToken} />

          <button
            onClick={handleSubmit}
            data-testid="feedback-submit"
            disabled={isSubmitting || !rating || !turnstileToken}
            className="w-full px-4 py-2 rounded-lg font-semibold text-white shadow-sm hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 transition-opacity"
            style={{ backgroundColor: 'rgb(var(--color-primary))' }}
          >
            {isSubmitting ? 'Submitting...' : (labels?.submit ?? 'Submit Feedback')}
          </button>
        </div>
      )}
      {error && <p className="text-center text-error" role="alert">{error}</p>}
    </div>
  );
}
