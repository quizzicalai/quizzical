// src/components/quiz/AnswerTile.tsx
import React, { memo, useState, useEffect } from 'react';
import clsx from 'clsx';
import type { Answer } from '../../types/quiz';
import { safeImageUrl } from '../../utils/safeImageUrl';
import { useFeatures } from '../../context/ConfigContext';
type AnswerTileProps = {
  answer: Answer;
  disabled?: boolean;
  isSelected?: boolean;
  onClick: (id: string) => void;
  /**
   * All-or-none image gate, decided by the parent AnswerGrid: an answer image
   * is only rendered when EVERY answer in the set has a valid image (owner
   * rule: all answers have images or none do — never a ragged grid). Defaults
   * to true so standalone usage keeps its self-contained behavior.
   */
  showImage?: boolean;
};

export const AnswerTile = memo(function AnswerTile({
  answer,
  disabled = false,
  isSelected = false,
  onClick,
  showImage = true,
}: AnswerTileProps) {
  const [imageError, setImageError] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  useEffect(() => { setImageError(false); setImageLoaded(false); }, [answer.imageUrl]);

  // DRAFT Q&A imagery gate — when the backend feature flag is off we ignore any
  // bound image and render a clean text-only tile (NO image element). See the
  // image-slot comment below: the text-only empty state is INTENTIONAL and
  // unified across the flag (owner directive: never a placeholder image).
  const { qaImages } = useFeatures();

  // §9.7.2 — defence-in-depth: only render https URLs from allowlisted hosts.
  // Gated additionally by the parent's all-or-none `showImage` decision.
  const safeUrl = qaImages && showImage ? safeImageUrl(answer.imageUrl) : null;

  const handleClick = () => { if (!disabled) onClick(answer.id); };
  const handleImageError = () => { setImageError(true); setImageLoaded(true); };
  const handleImageLoad = () => setImageLoaded(true);
  const shouldRenderImage = !!safeUrl && !imageError;

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
      className={clsx(
        'w-full',
        // Unified subtle-grey resting border (--color-border / slate-200) via
        // the shared `border-border` token — consistent with cards, inputs,
        // chips and the feedback card. Replaces the prior near-black borders
        // (inline muted/0.55 rest + hover:border-fg + border-fg/30 selected).
        'group relative text-left rounded-2xl border border-border bg-card p-4 sm:p-5 select-none',
        'transition-[transform,box-shadow,border-color,background-color] duration-150 ease-out-token',
        'shadow-sm',
        !disabled ? 'cursor-pointer' : 'cursor-not-allowed',
        // Hover deepens the SAME grey token slightly (slate-300-ish) — never black.
        !disabled && 'hover:-translate-y-0.5 hover:shadow-md hover:border-muted/40',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-card',
        !disabled && 'active:translate-y-0 active:shadow-sm active:scale-[0.995]',
        disabled && !isSelected && 'opacity-60',
        // Selection is carried by the primary ring (intentional focus/selection
        // colour) + shadow; the border stays the subtle grey token.
        isSelected && 'ring-2 ring-ring border-border shadow-md'
      )}
    >
      {isSelected && (
        <span className="absolute right-3 top-3 rounded-full bg-primary px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-white shadow-sm">
          Selected
        </span>
      )}

      {/* AC-UX-2026-05-25-PART3 item 5 — no in-tile spinner overlay while the
          agent thinks. The selected ring + aria-busy convey "this is your
          pick"; the top-right ThinkingIndicator carries the agent's busy
          state. Duplicating a spinner on the tile pulled the eye away from
          the upper-right status row. (This component is the canonical answer
          tile rendered by AnswerGrid.) */}

      {/* Blackbox fix #6 — NEVER a placeholder image. When there is no real image
          (flag off, unbound, or a load error) we render NO image element at all:
          the tile collapses to a clean text-only tile (matching QuestionImage,
          which already returns null).

          INTENTIONAL behaviour change, applied REGARDLESS of the qaImages flag
          (owner directive: never a placeholder image). This is NOT a flag-gating
          bug — do not "restore" a Logo here. It is the ONE deliberate departure
          from the prior flag-OFF behaviour: previously the empty slot rendered a
          128px Quizzical Logo box on EVERY answer tile, which read as a broken/
          missing image (especially next to a real-image tile). The empty state
          is now text-only whether the flag is OFF or ON-with-no-URL.

          The fixed-size slot is reserved ONLY when there is an image to show, so
          there is no layout shift and text-only tiles size to their content. */}
      {shouldRenderImage && (
        <div className="mb-3 h-32 w-full rounded-md overflow-hidden flex items-center justify-center relative">
          {/* M5: skeleton pulse while image is loading */}
          {!imageLoaded && (
            <div className="absolute inset-0 animate-pulse bg-muted/20 rounded-md" aria-hidden="true" />
          )}
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
        </div>
      )}

      {/* Answer label: same font family as landing subtitle, but smaller */}
      <span className="font-sans font-medium text-fg leading-tight">
        {answer.text}
      </span>
    </button>
  );
});
