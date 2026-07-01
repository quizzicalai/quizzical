import React, { useEffect, useRef } from 'react';
import type { ResultProfileData } from '../../types/result';
import type { ResultPageConfig } from '../../types/config';
import { safeImageUrl } from '../../utils/safeImageUrl';

type BlendedProfileResultProps = {
  result: ResultProfileData | null;
  labels?: Partial<ResultPageConfig>;
};

/** Clamp an arbitrary emphasis to a sane 0–100 bar width. */
function clampEmphasis(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

/** Compact blend label, e.g. "D/C" from primary/secondary initials. */
function blendLabel(primary: string, secondary?: string | null): string {
  const p = (primary ?? '').trim();
  if (!p) return '';
  const s = (secondary ?? '').trim();
  return s ? `${p[0].toUpperCase()}/${s[0].toUpperCase()}` : p[0].toUpperCase();
}

/**
 * Profile-style result view for `resultKind === 'blended_profile'` (the gated
 * DISC pilot). Renders the canonical dimensions with their relative emphasis,
 * the identified primary (+ secondary) blend, and the cohesive narrative that
 * explains the blend — instead of a single-character writeup.
 *
 * On-brand: reuses the same design tokens, focus handling, and prose measure as
 * the single-character ResultProfile. Reduced-motion-safe (only the global
 * token transitions / fade, which are neutralized under prefers-reduced-motion)
 * and AA-contrast: body/fg copy uses text-fg, and ALL secondary copy (the
 * emphasis %, blend summary, secondary label, per-dimension blurb) routes
 * through the dedicated --color-text-secondary token (slate-600, 7.58:1) — NOT
 * text-muted (slate-400) which fails AA. The emphasis bars are presentational —
 * each carries a textual percentage so the information is never colour-only.
 */
export function BlendedProfileResult({ result, labels = {} }: BlendedProfileResultProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  const title = result?.profileTitle ?? '';
  const imageUrl = safeImageUrl(result?.imageUrl) ?? undefined;
  const imageAlt = result?.imageAlt ?? title;
  const profile = result?.profile;
  const narrative = profile?.narrative ?? result?.summary ?? '';
  const dimensions = Array.isArray(profile?.dimensions) ? profile!.dimensions : [];

  useEffect(() => {
    if (title) headingRef.current?.focus();
  }, [title]);

  if (!result || !profile) return null;

  const label = blendLabel(profile.primary, profile.secondary);
  const blendSummary = profile.secondary
    ? `Primary: ${profile.primary} · Secondary: ${profile.secondary}`
    : `Primary: ${profile.primary}`;

  return (
    <article aria-labelledby="result-heading" data-testid="blended-profile-result">
      <header className="mb-4 text-center">
        <h1
          id="result-heading"
          ref={headingRef}
          tabIndex={-1}
          className="font-display text-2xl sm:text-3xl font-semibold tracking-tight text-fg outline-none"
        >
          {labels.titlePrefix ? `${labels.titlePrefix} ${title}` : title}
        </h1>
        {/* Blend chip: the primary/secondary at a glance. */}
        {label && (
          <p
            data-testid="blend-label"
            className="mt-2 inline-flex items-center rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-sm font-semibold text-primary"
          >
            {label} blend
          </p>
        )}
        {/* AA-corrected secondary copy (slate-600, 7.58:1) — text-muted is
            slate-400 and fails WCAG AA, which would also undermine the
            colour-blind affordance these numbers provide. */}
        <p className="mt-2 text-sm text-[rgb(var(--color-text-secondary,71_85_105))]">
          {blendSummary}
        </p>
      </header>

      {imageUrl && (
        <img
          src={imageUrl}
          alt={imageAlt}
          loading="lazy"
          className="mx-auto w-full max-w-md aspect-square object-cover rounded-xl shadow-sm mb-6 animate-fade-in"
        />
      )}

      {/* Dimensions: per-style emphasis + blurb. The list is the heart of a
          blended result — it shows the PROFILE across all canonical members
          rather than a single pick. */}
      {dimensions.length > 0 && (
        <section className="mt-2 text-left" aria-label="Profile dimensions">
          <h2 className="text-lg font-semibold tracking-tight text-fg mb-3 text-center">
            {labels.traitListTitle ?? 'Your profile across the dimensions'}
          </h2>
          <ul role="list" className="mx-auto w-full max-w-xl space-y-4">
            {dimensions.map((d, i) => {
              const pct = clampEmphasis(d.emphasis);
              const isPrimary = d.name === profile.primary;
              const isSecondary = !!profile.secondary && d.name === profile.secondary;
              return (
                <li key={`${d.name}-${i}`} className="min-w-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-sm font-semibold text-fg break-words">
                      {d.name}
                      {isPrimary && (
                        <span className="ml-2 text-xs font-medium text-primary">
                          primary
                        </span>
                      )}
                      {isSecondary && (
                        <span className="ml-2 text-xs font-medium text-[rgb(var(--color-text-secondary,71_85_105))]">
                          secondary
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 text-sm tabular-nums text-[rgb(var(--color-text-secondary,71_85_105))]">
                      {pct}
                    </span>
                  </div>
                  {/* Emphasis bar — presentational; the numeric % above and the
                      role/aria-* below carry the value for AT and colour-blind
                      users (never colour-only). */}
                  <div
                    role="meter"
                    aria-label={`${d.name} emphasis`}
                    aria-valuenow={pct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    className="mt-1 h-2 w-full overflow-hidden rounded-full bg-muted/30"
                  >
                    <div
                      className={`h-full rounded-full transition-[width] duration-slow ease-out-token ${
                        isPrimary ? 'bg-primary' : 'bg-primary/60'
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  {d.blurb && (
                    <p className="mt-1.5 text-sm text-[rgb(var(--color-text-secondary,71_85_105))] break-words">
                      {d.blurb}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* Cohesive blend narrative — same comfortable measure + pre-line wrap as
          the single-character summary. */}
      {narrative && (
        <div
          data-testid="blended-narrative"
          className="mx-auto mt-8 w-full min-w-0 max-w-prose font-sans text-sm sm:text-base text-fg/90 leading-relaxed whitespace-pre-line break-words text-left"
        >
          <p>{narrative}</p>
        </div>
      )}
    </article>
  );
}
