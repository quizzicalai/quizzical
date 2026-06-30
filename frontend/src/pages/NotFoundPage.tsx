// src/pages/NotFoundPage.tsx
import React from 'react';
import { Link } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { HeroCard } from '../components/layout/HeroCard';

/**
 * A robust 404 page that gracefully handles missing configuration.
 *
 * UI (HITLIST-2026-06-30) — aligned to the hero-card + design-token system (was
 * a bare `h-full` flex box). The big "404" uses the AA-safe `text-accent`
 * (amber-600, 3.19:1 — clears the 3:1 large-text threshold), the subheading
 * uses the AA secondary text token, and the home CTA matches the LandingPage
 * primary button (primary fill + `hover:opacity-95` instead of the prior
 * white-on-amber hover that failed the 4.5:1 normal-text threshold). >=44px tap
 * target.
 */
const NotFoundPage: React.FC = () => {
  const { config } = useConfig();

  // Safely access config with fallbacks for all text content.
  const heading = config?.content?.notFoundPage?.heading || 'Page Not Found';
  const subheading =
    config?.content?.notFoundPage?.subheading ||
    "Sorry, we couldn't find the page you're looking for.";
  const buttonText = config?.content?.notFoundPage?.buttonText || 'Go Back Home';

  return (
    <HeroCard ariaLabel="Page not found">
      <h1 className="font-display text-5xl sm:text-6xl font-extrabold text-accent mb-4">
        404
      </h1>
      <h2 className="font-display text-2xl font-bold text-primary mb-2">
        {heading}
      </h2>
      <p className="mx-auto mb-8 max-w-sm text-[rgb(var(--color-text-secondary,71_85_105))]">
        {subheading}
      </p>
      <Link
        to="/"
        style={{
          backgroundColor: 'rgb(var(--color-primary, 79 70 229))',
          color: 'rgb(255 255 255)',
        }}
        className="inline-flex w-full sm:w-auto min-h-[44px] items-center justify-center rounded-xl px-6 py-2.5 text-sm font-semibold shadow-sm transition-[transform,box-shadow,opacity] duration-fast ease-out-token hover:opacity-95 hover:-translate-y-0.5 hover:shadow-md active:translate-y-0 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 focus-visible:ring-offset-card"
      >
        {buttonText}
      </Link>
    </HeroCard>
  );
};

export default NotFoundPage;
