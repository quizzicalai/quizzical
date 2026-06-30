// frontend/src/components/common/WhimsicalError.tsx
//
// Whimsical-error-system (owner request, 2026-06-30) — the single reusable
// fail-state display.
//
// Renders:
//   * the WHIMSICAL, on-brand message (friendly; alludes to the cause), and
//   * the precise error CODE as LIGHT-GREY small text below it (for support
//     triage — the user can read it to us / paste it into a report).
//
// Both backend failures (which arrive with a `QF-...` code + whimsical message
// in the response envelope) and FE-only failures (the error boundary, a config
// load failure — see config/feErrorCodes.ts, which uses the same `QF-FE-...`
// scheme) flow through THIS one component.
//
// Design: design-token driven, minimal, no animation (so it is inherently
// reduced-motion safe). Light-grey = the existing secondary/muted token
// (`--color-text-secondary`, slate-600) one notch lighter via opacity so the
// code reads as a quiet, secondary tag — never competing with the message.

import React from 'react';

export type WhimsicalErrorProps = {
  /** The on-brand, user-facing message. Required (callers always have a fallback). */
  message: string;
  /** The precise `QF-...` code, rendered light-grey below the message. */
  code?: string;
  /** Optional heading above the message. */
  title?: string;
  /**
   * Optional backend-echoed trace id, surfaced (also light-grey) next to the
   * code so support can correlate logs. Hidden when absent.
   */
  traceId?: string;
  /** Optional primary action (e.g. Retry / Start Over / Go Home). */
  primaryCta?: {
    label: string;
    onClick: () => void;
  };
  /** Layout hint. `page` centers in a hero-style block; `inline` is compact. */
  variant?: 'page' | 'inline';
  className?: string;
};

const MUTED_STYLE: React.CSSProperties = {
  // Light-grey = the existing secondary/muted token, rendered quietly so the
  // code never competes with the whimsical message above it.
  color: 'rgb(var(--color-text-secondary, 71 85 105))',
  opacity: 0.7,
};

/**
 * Reusable whimsical fail-state. Whimsical message (friendly) + the code as
 * light-grey small text below it.
 */
export const WhimsicalError: React.FC<WhimsicalErrorProps> = ({
  message,
  code,
  title = 'Well, this is unexpected',
  traceId,
  primaryCta,
  variant = 'page',
  className,
}) => {
  const isPage = variant === 'page';

  return (
    <section
      role="alert"
      aria-live="polite"
      data-testid="whimsical-error"
      className={[
        'outline-none',
        isPage ? 'flex flex-col items-center justify-center text-center' : 'text-left',
        className ?? '',
      ]
        .join(' ')
        .trim()}
    >
      {title && (
        <h1
          className={
            isPage
              ? 'font-display text-2xl sm:text-3xl font-extrabold text-accent mb-3'
              : 'font-display text-lg font-bold text-accent mb-1'
          }
        >
          {title}
        </h1>
      )}

      <p
        className={
          isPage
            ? 'mx-auto mb-4 max-w-md text-[rgb(var(--color-text-secondary,71_85_105))]'
            : 'mb-2 text-sm text-[rgb(var(--color-text-secondary,71_85_105))]'
        }
        data-testid="whimsical-error-message"
      >
        {message}
      </p>

      {/* Light-grey code + optional trace id for support triage. Small, muted,
          monospace so it reads as a quiet diagnostic tag, never as body copy. */}
      {(code || traceId) && (
        <p
          className="text-xs font-mono select-all mb-6"
          style={MUTED_STYLE}
          data-testid="whimsical-error-code"
        >
          {code}
          {code && traceId ? ' · ' : ''}
          {traceId ? `ref ${traceId}` : ''}
        </p>
      )}

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
    </section>
  );
};

export default WhimsicalError;
