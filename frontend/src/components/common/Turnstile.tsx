// frontend/src/components/common/Turnstile.tsx
import React, { useEffect, useRef, useState, useCallback } from 'react';
import type { TurnstileProps, TurnstileOptions } from '../../types/turnstile';

const Turnstile: React.FC<TurnstileProps> = ({
  onVerify,
  onError,
  onExpire,
  theme = 'auto',
  size = 'normal',
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handleCallback = useCallback((token: string) => {
    console.log('[Turnstile] Token received');
    onVerify(token);
  }, [onVerify]);

  const handleError = useCallback(() => {
    console.error('[Turnstile] Verification error');
    setError('Verification failed. Please try again.');
    onError?.();
  }, [onError]);

  const handleExpired = useCallback(() => {
    console.log('[Turnstile] Token expired');
    onExpire?.();
  }, [onExpire]);

  useEffect(() => {
    let mounted = true;
    let retryCount = 0;
    const maxRetries = 10;
    const retryDelay = 500;

    const initTurnstile = () => {
      if (!mounted || !ref.current) return;

      if (window.turnstile) {
        try {
          if (widgetIdRef.current) {
            window.turnstile.remove(widgetIdRef.current);
          }

          const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY;
          if (!siteKey) {
            setError('Turnstile site key not configured');
            setIsLoading(false);
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

          widgetIdRef.current = window.turnstile.render(ref.current, options);
          setIsLoading(false);
          setError(null);
        } catch (err) {
          console.error('[Turnstile] Render error:', err);
          setError('Failed to load verification widget');
          setIsLoading(false);
        }
      } else if (retryCount < maxRetries) {
        retryCount++;
        setTimeout(initTurnstile, retryDelay);
      } else {
        setError('Turnstile script failed to load');
        setIsLoading(false);
      }
    };

    initTurnstile();

    return () => {
      mounted = false;
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch (err) {
          console.error('[Turnstile] Cleanup error:', err);
        }
      }
    };
  }, [handleCallback, handleError, handleExpired, theme, size]);

  useEffect(() => {
    (window as any).resetTurnstile = () => {
      if (widgetIdRef.current && window.turnstile) {
        window.turnstile.reset(widgetIdRef.current);
      }
    };
  }, []);

  if (error) {
    return (
      <div className="text-red-600 text-sm text-center p-2">
        {error}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center">
      {isLoading && (
        <div className="text-muted text-sm mb-2">Loading verification...</div>
      )}
      <div ref={ref} />
    </div>
  );
};

export default Turnstile;
