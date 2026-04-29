import React from 'react';
import clsx from 'clsx';

type HeroCardProps = {
  children: React.ReactNode;
  className?: string;        // optional extra classes for the outer card
  contentClassName?: string; // optional classes for the inner content wrapper
  /** For a11y/test visibility; defaults to 'Landing hero card' */
  ariaLabel?: string;
  /** Reserved for future hero adornments; currently no decorative icon is rendered. */
  showHero?: boolean;
};

export const HeroCard: React.FC<HeroCardProps> = React.memo(function HeroCard({
  children,
  className,
  contentClassName,
  ariaLabel = 'Landing hero card',
}) {
  return (
    <div className="flex-grow flex items-start justify-center p-3 sm:p-4 lp-wrapper" data-testid="hero-card-wrapper">
      <div
        className={clsx(
          'hero-surface w-full mx-auto lp-card flex flex-col justify-center border border-slate-200',
          'pt-6 sm:pt-8 md:pt-10 lg:pt-12',
          'pb-8 sm:pb-10 md:pb-12 lg:pb-14',
          'min-h-[40vh] sm:min-h-[44vh] md:min-h-[48vh] lg:min-h-[52vh]',
          className
        )}
        role="region"
        aria-label={ariaLabel}
        data-testid="hero-card"
      >
        <div className={clsx('text-center', contentClassName)} data-testid="hero-card-content">
          {children}
        </div>
      </div>
    </div>
  );
});
