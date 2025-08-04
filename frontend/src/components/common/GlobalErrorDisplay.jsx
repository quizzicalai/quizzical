import React, { useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { Logo } from './Logo'; // Assuming a Logo component is available for the 'page' variant

const IS_DEV = import.meta.env.DEV === true;

/**
 * A flexible, presentational component for displaying application errors.
 * It supports different variants, handles normalized error objects, and is fully accessible.
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
}) {
  const containerRef = useRef(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    if (autoFocus && error) {
      containerRef.current?.focus();
    }
  }, [autoFocus, error]);

  const { title, message, isRecoverable } = useMemo(() => {
    const isRec = Boolean(error?.retriable);
    const t = labels.title ?? 'An Error Occurred';
    const msg = error?.message ?? (isRec ? 'Please try again.' : 'An unexpected error occurred.');
    return { title: t, message: msg, isRecoverable: isRec };
  }, [error, labels]);

  if (!error) {
    return null;
  }

  return (
    <section
      ref={containerRef}
      tabIndex={-1}
      role="alert"
      aria-live="polite"
      className={clsx(
        'outline-none',
        variant === 'page' && 'flex flex-col items-center justify-center text-center h-screen p-4',
        variant === 'banner' && 'w-full p-4 bg-red-50 border-b border-red-200',
        variant === 'inline' && 'w-full p-4 border border-red-200 rounded-lg bg-red-50',
        className
      )}
    >
      <div className={clsx(variant === 'page' && 'max-w-md')}>
        {variant === 'page' && <Logo className="h-12 w-12 mx-auto mb-4 text-red-500" />}
        
        <div className="flex items-start gap-3">
          {variant !== 'page' && (icon || <span className="text-red-600 mt-1">⚠️</span>)}
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-red-800">{title}</h3>
            <p className="mt-1 text-sm text-red-700">{message}</p>
            {IS_DEV && error?.code && (
              <p className="mt-1 text-xs text-red-500 font-mono">
                Code: {error.code} {error.status && `(Status: ${error.status})`}
              </p>
            )}
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {isRecoverable && onRetry && (
            <button type="button" className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-md hover:bg-red-700" onClick={onRetry}>
              {labels.retry ?? 'Try Again'}
            </button>
          )}
          {!isRecoverable && onStartOver && (
            <button type="button" className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-md hover:bg-red-700" onClick={onStartOver}>
              {labels.startOver ?? 'Start Over'}
            </button>
          )}
          {onHome && (
             <button type="button" className="px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50" onClick={onHome}>
              {labels.home ?? 'Go Home'}
            </button>
          )}
          {IS_DEV && error?.details && (
            <button type="button" className="text-sm text-gray-500 hover:underline" onClick={() => setShowDetails(!showDetails)}>
              {showDetails ? 'Hide Details' : 'Show Details'}
            </button>
          )}
        </div>

        {IS_DEV && showDetails && error?.details && (
          <pre className="mt-4 max-h-48 overflow-auto rounded-md bg-gray-800 p-3 text-left text-xs text-white">
            {typeof error.details === 'string' ? error.details : JSON.stringify(error.details, null, 2)}
          </pre>
        )}
      </div>
    </section>
  );
}