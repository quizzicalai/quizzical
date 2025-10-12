import React, { useState, useCallback } from 'react';
import * as api from '../../services/apiService';
import clsx from 'clsx';
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
    } catch (e: any) {
      setError(e.message || 'Failed to submit feedback. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [quizId, rating, comment, isSubmitting, turnstileToken, labels?.turnstileError]);

  if (submitted) {
    return (
      <p className="text-center text-green-700 font-medium p-4 bg-green-50 rounded-md" role="status">
        {labels?.thanks ?? 'Thank you for your feedback!'}
      </p>
    );
  }

  return (
    // No container border/outline
    <div className="p-4 rounded-xl space-y-4">
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
              aria-pressed={isActive}
              disabled={isSubmitting}
              className={clsx(
                'h-12 w-12 sm:h-14 sm:w-14',
                'inline-flex items-center justify-center rounded-full',
                'border border-muted/40 bg-card text-fg shadow-sm',
                'hover:bg-bg hover:shadow-md active:scale-[0.98]',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                'transition-all duration-150',
                isActive && 'ring-2 ring-primary bg-primary/10 text-primary border-primary/30 scale-105',
                isSubmitting && 'opacity-60'
              )}
              aria-label={r === 'up'
                ? (labels?.thumbsUp ?? 'Good')
                : (labels?.thumbsDown ?? 'Bad')
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
          />

          <Turnstile onVerify={setTurnstileToken} />

          <button
            onClick={handleSubmit}
            disabled={isSubmitting || !rating || !turnstileToken}
            className="w-full px-4 py-2 rounded-lg font-semibold text-white shadow-sm hover:opacity-90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            style={{ backgroundColor: 'rgb(var(--color-primary))' }}
          >
            {isSubmitting ? 'Submitting...' : (labels?.submit ?? 'Submit Feedback')}
          </button>
        </div>
      )}
      {error && <p className="text-center text-red-600" role="alert">{error}</p>}
    </div>
  );
}
