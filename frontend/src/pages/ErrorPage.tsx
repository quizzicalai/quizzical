import React from 'react';
import { HeroCard } from '../components/layout/HeroCard';
import { WhimsicalError } from '../components/common/WhimsicalError';

interface ErrorPageProps {
  title?: string;
  message?: string;
  /**
   * Whimsical-error-system (2026-06-30) — the precise `QF-...` code, rendered as
   * light-grey small text below the message by WhimsicalError (support triage).
   */
  code?: string;
  /** Optional backend-echoed trace id, surfaced (light-grey) for log correlation. */
  traceId?: string;
  primaryCta?: {
    label: string;
    onClick: () => void;
  };
}

/**
 * Page-level error display. Whimsical-error-system (2026-06-30): now delegates to
 * the reusable `WhimsicalError` component so the friendly message + light-grey
 * `QF-...` code render consistently everywhere. Still wrapped in the shared
 * hero-card + design-token system. The CTA matches the LandingPage primary
 * button; >=44px tap target; AA-safe tokens.
 */
export const ErrorPage: React.FC<ErrorPageProps> = ({
  title = 'Something went wrong',
  message = "We're sorry, but an unexpected error occurred. Please try again later.",
  code,
  traceId,
  primaryCta,
}) => {
  return (
    <HeroCard ariaLabel="Error">
      <WhimsicalError
        variant="page"
        title={title}
        message={message}
        code={code}
        traceId={traceId}
        primaryCta={primaryCta}
      />
    </HeroCard>
  );
};
