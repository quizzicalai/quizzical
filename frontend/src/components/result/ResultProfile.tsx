// src/components/result/ResultProfile.tsx
import React, { useEffect, useRef, useState } from 'react';
import type { ResultProfileData } from '../../types/result';
import type { ResultPageConfig } from '../../types/config';

type ResultProfileProps = {
  result: ResultProfileData | null;
  labels?: Partial<ResultPageConfig>;
  shareUrl?: string;
  onCopyShare?: () => void;
  onStartNew?: () => void;
};

export function ResultProfile({
  result,
  labels = {},
  shareUrl,
  onCopyShare,
  onStartNew,
}: ResultProfileProps) {
  // --- Hooks must be unconditional ---
  const [copied, setCopied] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Derive display fields safely even if result is null (so hooks can still run)
  const title = result?.profileTitle ?? '';
  const summary = result?.summary ?? '';
  const imageUrl =
    typeof result?.imageUrl === 'string' && result.imageUrl.trim() !== ''
      ? result.imageUrl
      : undefined;
  const imageAlt = result?.imageAlt ?? title;
  const traits = result?.traits ?? [];

  useEffect(() => {
    // Focus only when we have a non-empty title
    if (title) headingRef.current?.focus();
  }, [title]);

  const handleCopy = async () => {
    if (!shareUrl || !onCopyShare) return;
    try {
      await onCopyShare();
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // no-op for v0
    }
  };

  // Safe early return AFTER hooks
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
          {labels.titlePrefix ? `${labels.titlePrefix} ${title}` : title}
        </h1>
      </header>

      {imageUrl && (
        <img
          src={imageUrl}
          alt={imageAlt}
          loading="lazy"
          className="w-full h-auto max-h-96 object-cover rounded-lg shadow-lg mb-6"
        />
      )}

      {summary && (
        <div className="prose max-w-none text-lg text-fg/90 whitespace-pre-line">
          <p>{summary}</p>
        </div>
      )}

      {traits.length > 0 && (
        <section className="mt-6">
          <h3 className="text-xl font-semibold mb-3">
            {labels.traitListTitle ?? 'Your Traits'}:
          </h3>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {traits.map((trait, idx) => (
              <li key={trait.id ?? idx} className="p-3 bg-bg border rounded-md">
                <strong className="block text-base text-fg">{trait.label}</strong>
                {trait.value && <span className="text-sm text-muted">{trait.value}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="mt-8 flex flex-wrap justify-center gap-4">
        {onStartNew && (
          <button
            type="button"
            onClick={onStartNew}
            className="px-6 py-3 bg-primary text-white font-semibold rounded-lg shadow-md hover:opacity-90 transition-opacity"
          >
            {labels.startOverButton ?? 'Start Another Quiz'}
          </button>
        )}

        {shareUrl && onCopyShare && (
          <button
            type="button"
            onClick={handleCopy}
            className="px-6 py-3 bg-secondary text-white font-semibold rounded-lg shadow-md hover:opacity-90 transition-opacity"
          >
            {copied ? (labels.shareCopied ?? 'Link Copied!') : (labels.shareButton ?? 'Share Result')}
          </button>
        )}
      </div>
    </article>
  );
}
