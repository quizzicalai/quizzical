// src/components/result/FeedbackIcons.tsx
import React, { useState, useCallback } from 'react';
import * as api from '../../services/apiService';
import clsx from 'clsx';
import type { ResultPageConfig } from '../../types/config';
import Turnstile from '../common/Turnstile'; // CORRECTED: Import Turnstile here

type FeedbackIconsProps = {
  quizId: string;
  labels?: ResultPageConfig['feedback'];
};

type Rating = 'up' | 'down';

export function FeedbackIcons({ quizId, labels = {} }: FeedbackIconsProps) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [comment, setComment] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null); // CORRECTED: State for the token

  const handleChoose = useCallback((newRating: Rating) => {
    if (submitted || isSubmitting) return;
    setRating(newRating);
    setError(null);
  }, [submitted, isSubmitting]);

  const handleSubmit = useCallback(async () => {
    if (!rating || isSubmitting) return;

    // CORRECTED: Check for Turnstile token before submitting
    if (!turnstileToken) {
      setError(labels.turnstileError ?? 'Please complete the security check before submitting.');
      return;
    }
    
    setIsSubmitting(true);
    setError(null);
    try {
      // CORRECTED: Pass the turnstileToken as the third argument
      await api.submitFeedback(quizId, { rating, comment }, turnstileToken);
      setSubmitted(true);
    } catch (e: any) {
      setError(e.message || 'Failed to submit feedback. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [quizId, rating, comment, isSubmitting, turnstileToken, labels.turnstileError]);

  if (submitted) {
    return (
      <p className="text-center text-green-700 font-medium p-4 bg-green-50 rounded-md" role="status">
        {labels.thanks ?? 'Thank you for your feedback!'}
      </p>
    );
  }

  return (
    <div className="p-4 border rounded-lg space-y-4">
      <p className="font-medium text-center text-fg">{labels.prompt ?? 'Was this result helpful?'}</p>
      <div className="flex justify-center gap-4">
        {(['up', 'down'] as Rating[]).map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => handleChoose(r)}
            aria-pressed={rating === r}
            disabled={isSubmitting}
            className={clsx(
              'p-3 rounded-full transition-colors border-2',
              rating === r ? 'bg-primary/20 border-primary' : 'bg-gray-100 hover:bg-gray-200',
              'focus:outline-none focus:ring-2 focus:ring-primary'
            )}
            aria-label={r === 'up' ? (labels.thumbsUp ?? 'Thumbs up') : (labels.thumbsDown ?? 'Thumbs down')}
          >
            {r === 'up' ? '👍' : '👎'}
          </button>
        ))}
      </div>
      {rating && (
        <div className="space-y-3 flex flex-col items-center">
          <label htmlFor="feedback-comment" className="sr-only">{labels.commentPlaceholder ?? 'Add a comment'}</label>
          <textarea
            id="feedback-comment"
            rows={3}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={labels.commentPlaceholder ?? 'Add a comment (optional)...'}
            className="w-full p-2 border rounded-md focus:ring-primary"
            disabled={isSubmitting}
          />

          {/* CORRECTED: Turnstile is rendered here, inside the form */}
          <Turnstile onVerify={setTurnstileToken} />

          <button
            onClick={handleSubmit}
            // CORRECTED: Button is disabled if there's no rating OR no Turnstile token
            disabled={isSubmitting || !rating || !turnstileToken}
            className="w-full px-4 py-2 bg-primary text-white rounded-md hover:opacity-90 disabled:opacity-50"
          >
            {isSubmitting ? 'Submitting...' : (labels.submit ?? 'Submit Feedback')}
          </button>
        </div>
      )}
      {error && <p className="text-center text-red-600" role="alert">{error}</p>}
    </div>
  );
}