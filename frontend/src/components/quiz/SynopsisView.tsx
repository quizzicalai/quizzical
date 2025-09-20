import React, { useEffect, useRef } from 'react';
import type { Synopsis } from '../../types/quiz';

type SynopsisViewProps = {
  synopsis: Synopsis | null;
  onProceed: () => void;
  isLoading: boolean;
  inlineError: string | null;
};

export function SynopsisView({ synopsis, onProceed, isLoading, inlineError }: SynopsisViewProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  if (!synopsis) {
    // Could render a loading skeleton here if desired
    return null;
  }

  return (
    <div className="max-w-3xl mx-auto text-center">
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

      <p className="text-lg text-fg/90 whitespace-pre-line mb-8">{synopsis.summary}</p>

      {/* Optional: show the generated characters if the backend provided them */}
      {Array.isArray(synopsis.characters) && synopsis.characters.length > 0 && (
        <section className="text-left mb-8">
          <h2 className="text-2xl font-semibold mb-4">Characters</h2>
          <ul className="grid gap-4 sm:grid-cols-2" aria-label="Generated characters">
            {synopsis.characters.map((c) => (
              <li
                key={c.name}
                className="rounded-lg border border-border/50 p-4 bg-background/60"
                aria-label={c.name}
              >
                <div className="flex items-start gap-3">
                  {c.imageUrl && (
                    <img
                      src={c.imageUrl}
                      alt=""
                      className="w-14 h-14 rounded object-cover shrink-0"
                      loading="lazy"
                    />
                  )}
                  <div>
                    <h3 className="text-base font-semibold text-fg">{c.name}</h3>
                    <p className="text-sm text-muted">{c.shortDescription}</p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-col items-center">
        <button
          type="button"
          onClick={onProceed}
          disabled={isLoading}
          className="w-full sm:w-auto px-8 py-3 bg-primary text-white rounded-lg text-lg font-semibold hover:opacity-90 transition-opacity disabled:opacity-60"
          aria-busy={isLoading || undefined}
        >
          {isLoading ? 'Loading…' : 'Start Quiz'}
        </button>

        {inlineError && (
          <p className="mt-4 text-red-600" role="alert">
            {inlineError}
          </p>
        )}
      </div>
    </div>
  );
}
