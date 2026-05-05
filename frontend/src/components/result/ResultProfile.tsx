import React, { useEffect, useRef, useState } from 'react';
import type { ResultProfileData } from '../../types/result';
import type { ResultPageConfig } from '../../types/config';
// NOTE: Replacing the SVG in this component will update the button globally.
import { ShareIcon } from '../../assets/icons/ShareIcon';
import { ArrowIcon } from '../../assets/icons/ArrowIcon';
import { safeImageUrl } from '../../utils/safeImageUrl';

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
  const [shared, setShared] = useState(false);
  const [copied, setCopied] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const title = result?.profileTitle ?? '';
  const summary = result?.summary ?? '';
  // §9.7.2 — defence-in-depth: only render https URLs from allowlisted hosts.
  const imageUrl = safeImageUrl(result?.imageUrl) ?? undefined;
  const imageAlt = result?.imageAlt ?? title;
  const traits = Array.isArray(result?.traits) ? result!.traits! : [];

  useEffect(() => {
    if (title) headingRef.current?.focus();
  }, [title]);

  const doCopy = async () => {
    if (!onCopyShare) return;
    setShareError(null);
    try {
      await onCopyShare();
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setShareError('Could not share this result right now. Please try again.');
    }
  };

  const handlePrimaryShare = async () => {
    if (!shareUrl) return;
    setShareError(null);
    // Prefer native share (mobile-like UX)
    if (navigator.share) {
      try {
        await navigator.share({
          title: title || 'My quiz result',
          url: shareUrl,
          text: labels.shareText ?? 'Check out my result!',
        });
        setShared(true);
        setTimeout(() => setShared(false), 1600);
        return;
      } catch {
        // fall through to copy fallback
      }
    }
    await doCopy();
  };

  if (!result) return null;

  return (
    <article aria-labelledby="result-heading">
      {/* Title – same font family as landing title, slightly smaller */}
      <header className="mb-6 text-center">
        <h1
          id="result-heading"
          ref={headingRef}
          tabIndex={-1}
          className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-fg outline-none"
        >
          {labels.titlePrefix ? `${labels.titlePrefix} ${title}` : title}
        </h1>
        <p role="status" aria-live="polite" className="sr-only">
          {shared
            ? labels.shared ?? 'Shared!'
            : copied
              ? labels.shareCopied ?? 'Link Copied!'
              : ''}
        </p>
      </header>

      {/* Optional cover image (for the result content itself). The BE
          generates this image as a 1024×1024 square so the result hero
          frames the matched character/outcome cleanly without cropping. */}
      {imageUrl && (
        <img
          src={imageUrl}
          alt={imageAlt}
          loading="lazy"
          className="mx-auto w-full max-w-sm aspect-square object-cover rounded-xl shadow-sm mb-6"
        />
      )}

      {/* Personality description – LEFT ALIGNED */}
      {summary && (
        <div className="font-sans text-sm sm:text-base text-fg/90 leading-relaxed whitespace-pre-line text-left">
          <p>{summary}</p>
        </div>
      )}

      {/* Traits – tighter grouping, no outlines; matches SynopsisView feel */}
      {traits.length > 0 && (
        <section className="mt-6 text-left">
          <h2 className="text-lg font-semibold tracking-tight text-fg mb-3">
            {labels.traitListTitle ?? 'Your Traits'}
          </h2>

          <ul
            role="list"
            aria-label="Result traits"
            className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0"
          >
            {traits.map((t, i) => {
              const showMobileSep = i >= 1;
              const showDesktopSep = i >= 2;
              return (
                <li key={`${t.id ?? i}-${t.label}`} className="p-3 sm:p-4">
                  {showMobileSep && (
                    <div className="block md:hidden mx-auto h-px bg-muted/40 w-16 sm:w-20 mb-3 sm:mb-4" />
                  )}
                  {showDesktopSep && (
                    <div className="hidden md:block mx-auto h-px bg-muted/40 w-20 lg:w-24 mb-3 sm:mb-4" />
                  )}
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-fg">{t.label}</h3>
                    {t.value && <p className="text-sm text-muted">{t.value}</p>}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* Actions */}
      <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center sm:gap-4">
        {/* Primary – Share
            NOTE: The icon comes from ../../assets/icons/ShareIcon.
            Updating that SVG updates this button across the app. */}
        {shareUrl && onCopyShare && (
          <button
            type="button"
            onClick={handlePrimaryShare}
            style={{ backgroundColor: 'rgb(var(--color-primary))' }}
            className="bg-primary inline-flex min-h-[44px] items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 rounded-xl text-base font-semibold text-white shadow-sm transition-transform duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 hover:opacity-95 active:translate-y-px"
            aria-label={
              shared
                ? labels.shared ?? 'Shared!'
                : copied
                  ? labels.shareCopied ?? 'Link Copied!'
                  : labels.shareButton ?? 'Share your result'
            }
          >
            <ShareIcon className="h-5 w-5" />
            {shared
              ? labels.shared ?? 'Shared!'
              : copied
                ? labels.shareCopied ?? 'Link Copied!'
                : labels.shareButton ?? 'Share your result'}
          </button>
        )}

        {/* Secondary – Start another quiz */}
        {onStartNew && (
          <button
            type="button"
            onClick={onStartNew}
            className="inline-flex min-h-[44px] items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 rounded-xl text-base font-semibold text-fg border border-muted/60 bg-card hover:bg-bg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <ArrowIcon className="h-5 w-5" />
            {labels.startOverButton ?? 'Start Another Quiz'}
          </button>
        )}
      </div>
      {shareError && (
        <p role="alert" className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {shareError}
        </p>
      )}
      {/* (Removed) tertiary “Copy link” affordance for a cleaner, non-repetitive CTA area */}
    </article>
  );
}
