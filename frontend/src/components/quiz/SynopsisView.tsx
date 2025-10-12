// frontend/src/components/quiz/SynopsisView.tsx

import React, { useEffect, useRef } from 'react';
import type { Synopsis, CharacterProfile } from '../../types/quiz';

type SynopsisViewProps = {
  synopsis: Synopsis | null;
  characters?: CharacterProfile[] | undefined;
  onProceed: () => void;
  onStartOver?: () => void;
  isLoading: boolean;
  inlineError: string | null;
};

export function SynopsisView({
  synopsis,
  characters,
  onProceed,
  onStartOver,
  isLoading,
  inlineError,
}: SynopsisViewProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  if (!synopsis) return null;

  const list: CharacterProfile[] | undefined =
    Array.isArray((synopsis as any).characters) && (synopsis as any).characters.length > 0
      ? (synopsis as any).characters
      : (Array.isArray(characters) && characters.length > 0 ? characters : undefined);

  return (
    <div className="max-w-3xl mx-auto text-center">
      <h1
        ref={headingRef}
        tabIndex={-1}
        aria-live="polite"
        className="text-4xl sm:text-5xl font-semibold font-serif text-fg mb-5 outline-none"
      >
        {synopsis.title}
      </h1>

      {synopsis.imageUrl && (
        <img
          src={synopsis.imageUrl}
          alt={synopsis.imageAlt || ''}
          loading="lazy"
          className="w-full h-64 object-cover rounded-xl my-6"
        />
      )}

      <p className="text-base text-fg/90 whitespace-pre-line mb-5">{synopsis.summary}</p>

      {/* Primary action directly under synopsis */}
      <div className="mb-8 flex flex-col items-center">
        <button
          type="button"
          onClick={onProceed}
          disabled={isLoading}
          style={{ backgroundColor: 'rgb(var(--color-primary))' }}
          className="inline-flex items-center justify-center w-full sm:w-auto px-6 py-3 rounded-xl text-base font-semibold text-white shadow-sm transition-transform duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 hover:opacity-95 active:translate-y-px disabled:opacity-60"
          aria-busy={isLoading || undefined}
        >
          {isLoading ? 'Loading…' : 'Start Quiz'}
        </button>

        {inlineError && (
          <p className="mt-3 text-red-600 text-sm" role="alert">
            {inlineError}
          </p>
        )}
      </div>

      {Array.isArray(list) && list.length > 0 && (
        <section className="text-left mb-12">
          <h2 className="text-2xl sm:text-3xl font-semibold tracking-tight text-fg mb-4">
            What’s your personality?
          </h2>

          {/* 1 column on small; 2 columns on md+. No vertical separators. */}
          <ul role="list" aria-label="Generated characters" className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-0">
            {list.map((c, i) => {
              const showMobileSep = i >= 1; // every item after the first on mobile
              const showDesktopSep = i >= 2; // rows 2+ on desktop (2 cols)

              return (
                <li key={c.name} className="p-4 sm:p-5">
                  {/* short, subtle separator line (doesn't affect cell width) */}
                  {showMobileSep && (
                    <div className="block md:hidden mx-auto h-px bg-muted/40 w-20 sm:w-24 mb-4 sm:mb-5" />
                  )}
                  {showDesktopSep && (
                    <div className="hidden md:block mx-auto h-px bg-muted/40 w-24 lg:w-28 mb-4 sm:mb-5" />
                  )}

                  <div className="flex items-start gap-3">
                    {c.imageUrl && (
                      <img
                        src={c.imageUrl}
                        alt=""
                        className="w-14 h-14 rounded-md object-cover shrink-0"
                        loading="lazy"
                      />
                    )}
                    <div className="min-w-0">
                      <h3 className="text-base font-semibold text-fg">{c.name}</h3>
                      <p className="text-sm text-muted">{c.shortDescription}</p>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <div className="mt-8">
        <button
          type="button"
          onClick={onStartOver}
          className="text-sm text-muted underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded"
        >
          Try another topic
        </button>
      </div>
    </div>
  );
}
