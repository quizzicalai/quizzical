// src/components/result/ResultProfile.jsx
import React, { useState, useEffect, useRef } from 'react';

export function ResultProfile({ result, labels, shareUrl, onCopyShare, onStartNew }) {
  const [copied, setCopied] = useState(false);
  const headingRef = useRef(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, [result?.profileTitle]);

  const handleCopy = async () => {
    if (!shareUrl || !onCopyShare) return;
    try {
      await onCopyShare();
      setCopied(true);
      setTimeout(() => setCopied(false), 2000); // Reset after 2 seconds
    } catch (err) {
      console.error("Failed to copy link", err);
    }
  };

  if (!result) return null;

  return (
    <article aria-labelledby="result-heading">
      <header className="text-center mb-6">
        <h1
          id="result-heading"
          ref={headingRef}
          tabIndex={-1}
          className="text-3xl sm:text-4xl font-bold text-fg outline-none"
        >
          {result.profileTitle}
        </h1>
      </header>

      {result.imageUrl && (
        <img
          src={result.imageUrl}
          alt={result.imageAlt ?? result.profileTitle}
          loading="lazy"
          className="w-full h-auto max-h-96 object-cover rounded-lg shadow-lg mb-6"
        />
      )}

      <div className="prose max-w-none text-lg text-text-color/90 whitespace-pre-line">
        <p>{result.summary}</p>
      </div>

      {result.traits?.length > 0 && (
        <div className="mt-6">
          <h3 className="text-xl font-semibold mb-3">Your Traits:</h3>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {result.traits.map((trait, index) => (
              <li key={trait.id || index} className="p-3 bg-background-color border rounded-md">
                <strong className="block text-base text-fg">{trait.label}</strong>
                {trait.value && <span className="text-sm text-muted">{trait.value}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-8 flex flex-wrap justify-center gap-4">
        <button
          type="button"
          onClick={onStartNew}
          className="px-6 py-3 bg-primary-color text-white font-semibold rounded-lg shadow-md hover:opacity-90 transition-opacity"
        >
          {labels?.startNew ?? 'Start Another Quiz'}
        </button>
        {shareUrl && (
          <button
            type="button"
            onClick={handleCopy}
            className="px-6 py-3 bg-secondary-color text-fg font-semibold rounded-lg shadow-md hover:opacity-90 transition-opacity"
          >
            {copied ? (labels?.copied ?? 'Link Copied!') : (labels?.copy ?? 'Share Result')}
          </button>
        )}
      </div>
    </article>
  );
}