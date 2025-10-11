// frontend/src/components/common/Turnstile.tsx
import React, { useEffect, useRef, useState, useCallback } from 'react';
import type { TurnstileProps, TurnstileOptions } from '../../types/turnstile';

const USE_DEV_MODE = import.meta.env.VITE_TURNSTILE_DEV_MODE === 'true';

type Props = TurnstileProps & {
  /** If true and size="invisible", execute immediately after render. */
  autoExecute?: boolean;
};

const Turnstile: React.FC<Props> = ({
  onVerify,
  onError,
  onExpire,
  theme = 'auto',
  size = 'invisible',
  autoExecute = true,
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleCallback = useCallback(
    (token: string) => {
      onVerify(token);
    },
    [onVerify]
  );

  const handleError = useCallback(() => {
    setError('Verification failed. Please try again.');
    onError?.();
  }, [onError]);

  const handleExpired = useCallback(() => {
    onExpire?.();
    // For invisible widgets, immediately try to re-execute to refresh token
    if (widgetIdRef.current && window.turnstile && size === 'invisible' && autoExecute) {
      try { window.turnstile.execute(widgetIdRef.current); } catch {
        // Intentionally ignore errors during execute
      }
    }
  }, [onExpire, autoExecute, size]);

  // Dev mode: issue a token immediately, with zero UI
  useEffect(() => {
    if (!USE_DEV_MODE) return;
    const timer = setTimeout(() => {
      handleCallback('dev-mode-token-' + Date.now());
    }, 50);
    return () => clearTimeout(timer);
  }, [handleCallback]);

  useEffect(() => {
    if (USE_DEV_MODE) return; // skip real init when bypassing

    let mounted = true;
    let retryCount = 0;
    const maxRetries = 10;
    const retryDelay = 300;

    const renderWidget = () => {
      if (!mounted || !ref.current || !window.turnstile) return;

      try {
        if (widgetIdRef.current) {
          window.turnstile.remove(widgetIdRef.current);
          widgetIdRef.current = null;
        }

        const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY;
        if (!siteKey) {
          setError('Turnstile site key not configured');
          return;
        }

        const options: TurnstileOptions = {
          sitekey: siteKey,
          callback: handleCallback,
          'error-callback': handleError,
          'expired-callback': handleExpired,
          theme,
          size,
        };

        const id = window.turnstile.render(ref.current, options);
        widgetIdRef.current = id;

        // For invisible widgets, run immediately to get a token
        if (size === 'invisible' && autoExecute) {
          try { window.turnstile.execute(id); } catch {
            // Intentionally ignore errors during execute
          }
        }

        setError(null);
      } catch (err) {
        setError('Failed to load verification widget');
      }
    };

    const waitForScript = () => {
      if (window.turnstile) {
        renderWidget();
        return;
      }
      if (retryCount >= maxRetries) {
        setError('Turnstile script failed to load');
        return;
      }
      retryCount += 1;
      setTimeout(waitForScript, retryDelay);
    };

    waitForScript();

    return () => {
      mounted = false;
      if (widgetIdRef.current && window.turnstile) {
        try { window.turnstile.remove(widgetIdRef.current); } catch {
          // Intentionally ignore errors during remove
        }
      }
    };
  }, [handleCallback, handleError, handleExpired, theme, size, autoExecute]);

  // Expose a “reset+execute” helper to callers (e.g., LandingPage after an API failure)
  useEffect(() => {
    (window as any).resetTurnstile = () => {
      if (USE_DEV_MODE) {
        handleCallback('dev-mode-token-reset-' + Date.now());
        return;
      }
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.reset(widgetIdRef.current);
          if (size === 'invisible' && autoExecute) {
            window.turnstile.execute(widgetIdRef.current);
          }
        } catch {
          // Intentionally ignore errors during reset+execute
        }
      }
    };
    return () => { delete (window as any).resetTurnstile; };
  }, [handleCallback, autoExecute, size]);

  // Error (plain text). Otherwise render only the container (invisible size shows no UI)
  if (error) {
    return <p className="text-red-600 text-sm mt-2">{error}</p>;
  }

  return <div ref={ref} data-testid="turnstile" />;
};

export default Turnstile;
