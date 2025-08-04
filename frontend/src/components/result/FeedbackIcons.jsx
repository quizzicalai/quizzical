// src/components/quiz/FeedbackIcons.jsx
import React, { useState, useCallback } from 'react';
import * as api from '../../services/apiService';
import clsx from 'clsx';

export function FeedbackIcons({ quizId, labels }) {
  const [rating, setRating] = useState(null);
  const [comment, setComment] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState(null);

  const handleChoose = useCallback((newRating) => {
    if (submitted || isSubmitting) return;
    setRating(newRating);
    setError(null);
  }, [submitted, isSubmitting]);

  const handleSubmit = useCallback(async () => {
    if (!rating || isSubmitting) return;
    
    setIsSubmitting(true);
    setError(null);
    try {
      await api.submitFeedback(quizId, { rating, comment });
      setSubmitted(true);
    } catch (e) {
      setError(e.message || 'Failed to submit feedback. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [quizId, rating, comment, isSubmitting]);

  if (submitted) {
    return (
      <p className="text-center text-green-700 font-medium p-4 bg-green-50 rounded-md" role="status">
        {labels?.submitted ?? 'Thank you for your feedback!'}
      </p>
    );
  }

  return (
    <div className="p-4 border rounded-lg space-y-4">
      <p className="font-medium text-center text-fg">{labels?.prompt ?? 'Was this result helpful?'}</p>
      <div className="flex justify-center gap-4">
        {['up', 'down'].map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => handleChoose(r)}
            aria-pressed={rating === r}
            disabled={isSubmitting}
            className={clsx(
              'p-3 rounded-full transition-colors border-2',
              rating === r ? 'bg-primary/20 border-primary-color' : 'bg-gray-100 hover:bg-gray-200',
              'focus:outline-none focus:ring-2 focus:ring-primary'
            )}
            aria-label={r === 'up' ? (labels?.up ?? 'Thumbs up') : (labels?.down ?? 'Thumbs down')}
          >
            {r === 'up' ? '👍' : '👎'}
          </button>
        ))}
      </div>
      {rating && (
        <div className="space-y-2">
          <label htmlFor="feedback-comment" className="sr-only">{labels?.addComment ?? 'Add a comment'}</label>
          <textarea
            id="feedback-comment"
            rows="3"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={labels?.addComment ?? 'Add a comment (optional)...'}
            className="w-full p-2 border rounded-md focus:ring-primary"
            disabled={isSubmitting}
          />
          <button
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="w-full px-4 py-2 bg-primary text-white rounded-md hover:opacity-90 disabled:opacity-50"
          >
            {isSubmitting ? 'Submitting...' : (labels?.submit ?? 'Submit Feedback')}
          </button>
        </div>
      )}
      {error && <p className="text-center text-red-600" role="alert">{error}</p>}
    </div>
  );
}