import React from 'react';
import { HeroCard } from '../components/layout/HeroCard';

interface ErrorPageProps {
  title?: string;
  message?: string;
  primaryCta?: {
    label: string;
    onClick: () => void;
  };
}

/**
 * UI (HITLIST-2026-06-30) — aligned to the hero-card + design-token system used
 * across the app (was a bare `h-full` flex box with a dated amber hover that
 * failed AA). The heading uses the AA-safe `text-accent` (amber-600, 3.19:1 —
 * clears the 3:1 large-text threshold), body text uses the AA secondary token,
 * and the CTA matches the LandingPage primary button (primary fill +
 * `hover:opacity-95` instead of the prior white-on-amber hover, which failed
 * the 4.5:1 normal-text threshold). >=44px tap target.
 */
export const ErrorPage: React.FC<ErrorPageProps> = ({
  title = 'Something went wrong',
  message = "We're sorry, but an unexpected error occurred. Please try again later.",
  primaryCta,
}) => {
  return (
    <HeroCard ariaLabel="Error">
      <h1 className="font-display text-3xl sm:text-4xl font-extrabold text-accent mb-4">
        {title}
      </h1>
      <p className="mx-auto mb-8 max-w-md text-[rgb(var(--color-text-secondary,71_85_105))]">
        {message}
      </p>
      {primaryCta && (
        <button
          type="button"
          onClick={primaryCta.onClick}
          style={{
            backgroundColor: 'rgb(var(--color-primary, 79 70 229))',
            color: 'rgb(255 255 255)',
          }}
          className="inline-flex w-full sm:w-auto min-h-[44px] items-center justify-center rounded-xl px-6 py-2.5 text-sm font-semibold shadow-sm transition-[transform,box-shadow,opacity] duration-fast ease-out-token hover:opacity-95 hover:-translate-y-0.5 hover:shadow-md active:translate-y-0 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 focus-visible:ring-offset-card"
        >
          {primaryCta.label}
        </button>
      )}
    </HeroCard>
  );
};
