// frontend/src/components/common/Turnstile.tsx
import React, { useEffect, useRef, useState, useCallback } from 'react';
import type { TurnstileProps, TurnstileOptions } from '../../types/turnstile';

const IS_DEV = import.meta.env.DEV === true;
const USE_DEV_MODE = IS_DEV && import.meta.env.VITE_TURNSTILE_DEV_MODE === 'true';

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

  // Development mode bypass
  useEffect(() => {
    if (USE_DEV_MODE) {
      console.log('[Turnstile] Development mode - bypassing verification');
      setIsLoading(false);
      setError(null);
      // Simulate successful verification after a short delay
      const timer = setTimeout(() => {
        handleCallback('dev-mode-token-' + Date.now());
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [handleCallback]);

  useEffect(() => {
    if (USE_DEV_MODE) return; // Skip real Turnstile in dev mode

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
        console.error('[Turnstile] Script failed to load after', maxRetries, 'retries');
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
      if (USE_DEV_MODE) {
        // In dev mode, just trigger a new verification
        handleCallback('dev-mode-token-' + Date.now());
        return;
      }
      
      if (widgetIdRef.current && window.turnstile) {
        window.turnstile.reset(widgetIdRef.current);
      }
    };
  }, [handleCallback]);

  if (error && !USE_DEV_MODE) {
    return (
      <div className="text-red-600 text-sm text-center p-2">
        {error}
        {IS_DEV && (
          <div className="mt-2 text-xs text-gray-500">
            💡 Tip: Set VITE_TURNSTILE_DEV_MODE=true in your .env.local to bypass Turnstile in development
          </div>
        )}
      </div>
    );
  }

  if (USE_DEV_MODE) {
    return (
      <div className="flex flex-col items-center">
        <div className="text-green-600 text-sm mb-2 p-2 bg-green-50 rounded border border-green-200">
          🔧 Development Mode - Security check bypassed
        </div>
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