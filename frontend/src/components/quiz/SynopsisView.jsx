// src/components/quiz/SynopsisView.jsx
import React, { useEffect, useRef } from 'react';

export function SynopsisView({ synopsis, onProceed, isLoading, inlineError }) {
  const headingRef = useRef(null);

  // When the synopsis loads, focus the heading.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  if (!synopsis) {
    return null; // Or a loading skeleton
  }

  return (
    <div className="max-w-2xl mx-auto text-center">
      <h1
        ref={headingRef}
        tabIndex={-1}
        aria-live="polite"
        className="text-3xl sm:text-4xl font-bold text-fg mb-4 outline-none"
      >
        {synopsis.title}
      </h1>

      {synopsis.imageUrl && (
        <img
          src={synopsis.imageUrl}
          alt={synopsis.imageAlt || ''}
          loading="lazy"
          className="w-full h-64 object-cover rounded-lg my-6"
        />
      )}

      <p className="text-lg text-text-color/90 whitespace-pre-line mb-8">
        {synopsis.summary}
      </p>

      <button
        type="button"
        onClick={onProceed}
        disabled={isLoading}
        className="w-full sm:w-auto px-8 py-3 bg-primary-color text-white rounded-lg text-lg font-semibold hover:opacity-90 transition-opacity disabled:opacity-60"
      >
        {isLoading ? 'Loading...' : 'Start Quiz'}
      </button>

      {inlineError && (
        <p className="mt-4 text-red-600" role="alert">
          {inlineError}
        </p>
      )}
    </div>
  );
}