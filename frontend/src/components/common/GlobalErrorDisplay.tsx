// src/components/common/GlobalErrorDisplay.tsx

import React, { useEffect, useMemo, useRef } from 'react';
import clsx from 'clsx';
import type { ErrorsConfig } from '../../types/config';
import type { ApiError } from '../../types/api';

// The props contract for this component. It is only used here,
// so it's fine to keep it in this file.
type GlobalErrorDisplayProps = {
  variant?: 'inline' | 'page' | 'banner';
  error: ApiError | null;
  labels?: Partial<ErrorsConfig>; // Use Partial since not all labels may be present
  onRetry?: () => void;
  onHome?: () => void;
  onStartOver?: () => void;
  icon?: React.ReactNode;
  autoFocus?: boolean;
  className?: string;
};

/**
 * A flexible, presentational component for displaying application errors.
 */
export function GlobalErrorDisplay({
  variant = 'inline',
  error,
  labels = {},
  onRetry,
  onHome,
  onStartOver,
  icon,
  autoFocus = true,
  className,
}: GlobalErrorDisplayProps) {
  const containerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (autoFocus && error) {
      containerRef.current?.focus();
    }
  }, [autoFocus, error]);

  const { title, message, isRecoverable, code, traceId } = useMemo(() => {
    const isRec = Boolean(error?.retriable);
    const t = labels.title ?? 'An Error Occurred';
    // Whimsical-error-system (2026-06-30): prefer the backend's on-brand
    // `whimsical` copy when present, falling back to the technical message.
    const msg =
      error?.whimsical ??
      error?.message ??
      (isRec ? 'Please try again.' : 'An unexpected error occurred.');
    return {
      title: t,
      message: msg,
      isRecoverable: isRec,
      code: error?.qfCode,
      traceId: error?.traceId,
    };
  }, [error, labels]);

  if (!error) {
    return null;
  }

  // Placeholder for the Logo since it doesn't exist yet
  const PageIcon = () => (
    <div className="mx-auto mb-4 h-12 w-12 flex items-center justify-center rounded-full bg-error-soft">
      <svg
        className="h-6 w-6 text-error"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
        />
      </svg>
    </div>
  );

  return (
    <section
      ref={containerRef}
      tabIndex={-1}
      role="alert"
      aria-live="polite"
      className={clsx(
        'outline-none',
        variant === 'page' && 'flex flex-col items-center justify-center text-center flex-grow min-h-[50vh] p-4',
        className
      )}
    >
      <div
        className={clsx(
          'rounded-xl border p-4',
          variant === 'banner'
            ? 'w-full bg-error-soft border-error-border'
            : variant === 'inline'
            ? 'w-full bg-error-soft border-error-border'
            : 'max-w-md'
        )}
      >
        {variant === 'page' && <PageIcon />}
        <div className="flex items-start gap-3">
          {variant !== 'page' && (icon || <span className="text-error mt-1">⚠️</span>)}
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-error-strong">{title}</h3>
            <p className="mt-1 text-sm text-error">{message}</p>
            {/* Whimsical-error-system (2026-06-30): the precise QF-... code as
                LIGHT-GREY small text for support triage (muted secondary token,
                quieted with opacity so it never competes with the message). */}
            {(code || traceId) && (
              <p
                className="mt-1 text-xs font-mono select-all"
                style={{
                  color: 'rgb(var(--color-text-secondary, 71 85 105))',
                  opacity: 0.7,
                }}
                data-testid="whimsical-error-code"
              >
                {code}
                {code && traceId ? ' · ' : ''}
                {traceId ? `ref ${traceId}` : ''}
              </p>
            )}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {isRecoverable && onRetry && (
            <button
              type="button"
              className="px-4 py-2 bg-error text-white text-sm font-medium rounded-md hover:bg-error-strong"
              onClick={onRetry}
            >
              {labels.retry ?? 'Try Again'}
            </button>
          )}
          {!isRecoverable && onStartOver && (
            <button
              type="button"
              className="px-4 py-2 bg-error text-white text-sm font-medium rounded-md hover:bg-error-strong"
              onClick={onStartOver}
            >
              {labels.startOver ?? 'Start Over'}
            </button>
          )}
          {/* Fallback so an error state is NEVER button-less: when no retry /
              start-over handler applies but a caller passed onHome (e.g.
              FinalPage on an expired/404 result), render a way out instead of
              dead-ending the user with browser-back as the only exit. */}
          {!(isRecoverable && onRetry) && !(!isRecoverable && onStartOver) && onHome && (
            <button
              type="button"
              className="px-4 py-2 bg-error text-white text-sm font-medium rounded-md hover:bg-error-strong"
              onClick={onHome}
            >
              {labels.startOver ?? 'Start Over'}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
