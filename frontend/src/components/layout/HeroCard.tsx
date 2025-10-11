import React from 'react';
import clsx from 'clsx';
import { WizardCatIcon } from '../../assets/icons/WizardCatIcon';

type HeroCardProps = {
  children: React.ReactNode;
  className?: string;        // optional extra classes for the outer card
  contentClassName?: string; // optional classes for the inner content wrapper
  /** For a11y/test visibility; defaults to 'Landing hero card' */
  ariaLabel?: string;
  /** Hide the hero area if needed elsewhere in the app later */
  showHero?: boolean;
};

export const HeroCard: React.FC<HeroCardProps> = React.memo(function HeroCard({
  children,
  className,
  contentClassName,
  ariaLabel = 'Landing hero card',
  showHero = true,
}) {
  return (
    <div className="flex-grow flex items-start justify-center p-4 sm:p-6 lp-wrapper" data-testid="hero-card-wrapper">
      <div
        className={clsx(
          'w-full mx-auto lp-card flex flex-col justify-center',
          // vertical rhythm and min-heights mirror LandingPage exactly
          'pt-4 sm:pt-6 md:pt-8 lg:pt-10',
          'pb-12 sm:pb-16 md:pb-20 lg:pb-24',
          'min-h-[50vh] sm:min-h-[55vh] md:min-h-[60vh] lg:min-h-[66vh]',
          className
        )}
        role="region"
        aria-label={ariaLabel}
        data-testid="hero-card"
      >
        <div className={clsx('text-center', contentClassName)} data-testid="hero-card-content">
          {showHero && (
            <div className="flex justify-center lp-space-after-hero" data-testid="hero-card-hero">
              <span className="lp-hero-wrap">
                <span className="lp-hero-blob" />
                <WizardCatIcon className="lp-hero" aria-label="Wizard cat reading a book" />
              </span>
            </div>
          )}
          {children}
        </div>
      </div>
    </div>
  );
});
