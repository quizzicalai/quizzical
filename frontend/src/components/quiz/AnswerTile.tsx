// src/components/quiz/AnswerTile.tsx
import React, { memo, useState, useEffect } from 'react';
import clsx from 'clsx';
import type { Answer } from '../../types/quiz';
import { Logo } from '../../assets/icons/Logo';
import { Spinner } from '../common/Spinner';
import { safeImageUrl } from '../../utils/safeImageUrl';
type AnswerTileProps = {
  answer: Answer;
  disabled?: boolean;
  isSelected?: boolean;
  onClick: (id: string) => void;
};

export const AnswerTile = memo(function AnswerTile({
  answer,
  disabled = false,
  isSelected = false,
  onClick,
}: AnswerTileProps) {
  const [imageError, setImageError] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  useEffect(() => { setImageError(false); setImageLoaded(false); }, [answer.imageUrl]);

  // §9.7.2 — defence-in-depth: only render https URLs from allowlisted hosts.
  const safeUrl = safeImageUrl(answer.imageUrl);

  const handleClick = () => { if (!disabled) onClick(answer.id); };
  const handleImageError = () => { setImageError(true); setImageLoaded(true); };
  const handleImageLoad = () => setImageLoaded(true);
  const showImage = !!safeUrl && !imageError;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      aria-pressed={isSelected}
      aria-busy={isSelected && disabled ? true : undefined}
      aria-label={
        isSelected
          ? `${answer.text} (currently selected)`
          : `Select answer: ${answer.text}`
      }
      // Match landing-pill base outline (muted 55%)
      style={{ borderColor: 'rgb(var(--color-muted) / 0.55)' }}
      className={clsx(
        'w-full',
        'group relative text-left rounded-2xl border bg-card p-4 sm:p-5 select-none',
        'transition-[transform,box-shadow,border-color,background-color] duration-150',
        'shadow-sm',
        !disabled ? 'cursor-pointer' : 'cursor-not-allowed',
        !disabled && 'hover:-translate-y-0.5 hover:shadow-md hover:border-fg',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-card',
        !disabled && 'active:translate-y-0 active:shadow-sm active:scale-[0.995]',
        disabled && !isSelected && 'opacity-60',
        isSelected && 'ring-2 ring-ring border-fg/30 shadow-md'
      )}
    >
      {isSelected && (
        <span className="absolute right-3 top-3 rounded-full bg-primary px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-white shadow-sm">
          Selected
        </span>
      )}

      {/* Busy overlay — themed color; does NOT change cursor */}
      {isSelected && disabled && (
        <div className="absolute inset-0 bg-card/60 flex items-center justify-center rounded-2xl text-fg cursor-default">
          <Spinner size="md" />
        </div>
      )}

      <div className="mb-3 h-32 w-full rounded-md overflow-hidden flex items-center justify-center relative">
        {/* M5: skeleton pulse while image is loading */}
        {showImage && !imageLoaded && (
          <div className="absolute inset-0 animate-pulse bg-muted/20 rounded-md" aria-hidden="true" />
        )}
        {showImage ? (
          <img
            src={safeUrl as string}
            alt={answer.imageAlt || `Image for: ${answer.text}`}
            className={clsx(
              'h-full w-full object-cover transition-[transform,opacity] duration-150 group-hover:scale-[1.02]',
              imageLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onError={handleImageError}
            onLoad={handleImageLoad}
            loading="lazy"
          />
        ) : (
          <Logo className="w-16 h-16 text-muted group-hover:text-fg transition-colors duration-150" />
        )}
      </div>

      {/* Answer label: same font family as landing subtitle, but smaller */}
      <span className="font-sans font-medium text-fg leading-tight">
        {answer.text}
      </span>
    </button>
  );
});
