// src/components/quiz/QuestionImage.tsx
import React, { memo, useEffect, useState } from 'react';
import clsx from 'clsx';

type QuestionImageProps = {
  /** Already-validated (safe) image URL, or null/undefined to render nothing. */
  src: string | null | undefined;
  alt: string;
};

/**
 * DRAFT — decorative, fail-open same-universe illustration shown above a quiz
 * question. Design goals (match the task's load-time bar):
 *   - Tiny + lazy: `loading="lazy"`, `decoding="async"`, capped render size.
 *   - Zero layout shift: a fixed-height slot is reserved ONLY when there is a
 *     URL to show; when there's no image (off / unbound / load error) the
 *     component renders nothing and the header collapses to today's layout.
 *   - Fail-open: on error we hide the image entirely (no cross-origin
 *     placeholder), so a dead URL never degrades the question.
 *   - Decorative: the meaningful content is the question text; the image is
 *     supplementary. `alt` is provided for screen readers but kept concise.
 */
export const QuestionImage = memo(function QuestionImage({ src, alt }: QuestionImageProps) {
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setError(false);
    setLoaded(false);
  }, [src]);

  if (!src || error) {
    return null;
  }

  return (
    <div className="mb-6 flex justify-center">
      {/* Fixed-size slot reserves space so the fade-in causes no CLS. */}
      <div className="relative h-28 w-28 sm:h-32 sm:w-32 overflow-hidden rounded-2xl bg-muted/10">
        {!loaded && (
          <div className="absolute inset-0 animate-pulse bg-muted/20" aria-hidden="true" />
        )}
        <img
          src={src}
          alt={alt}
          width={128}
          height={128}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          onError={() => setError(true)}
          className={clsx(
            'h-full w-full object-cover transition-opacity duration-200',
            loaded ? 'opacity-100' : 'opacity-0',
          )}
        />
      </div>
    </div>
  );
});
