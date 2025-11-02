import React, { useEffect, useRef, useState, useCallback } from 'react';
import type { TurnstileProps, TurnstileOptions } from '../../types/turnstile';

// Build-time toggles (still supported for local/dev)
const USE_DEV_MODE = (import.meta.env.VITE_TURNSTILE_DEV_MODE ?? 'false') === 'true';
const FALLBACK_SITE_KEY = (import.meta.env.VITE_TURNSTILE_SITE_KEY ?? '').trim();

// If you already have a ConfigContext, import its hook:
import { useConfig } from '../../context/ConfigContext'; // adjust path to your app

const Turnstile: React.FC<TurnstileProps> = ({
  onVerify,
  onError,
  onExpire,
  theme = 'auto',
  size = 'invisible',
  autoExecute = true,
}) => {
  // runtime config fetched from /config
  const { features } = useConfig();

  // Final policy:
  // - If backend says disabled -> bypass (emit token, render nothing)
  // - Else use a real widget; require a site key from /config or Vite fallback
  const TURNSTILE_DISABLED = features.turnstile === false;
  const SITE_KEY = (features.turnstileSiteKey ?? FALLBACK_SITE_KEY).trim();

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
    if (widgetIdRef.current && window.turnstile && size === 'invisible' && autoExecute) {
      try { window.turnstile.execute(widgetIdRef.current); } catch { /* ignore */ }
    }
  }, [onExpire, autoExecute, size]);

  // Hard bypass when backend disables Turnstile:
  useEffect(() => {
    if (!TURNSTILE_DISABLED) return;
    // Emit a benign "bypass" token so callers can proceed with the same API shape.
    const t = setTimeout(() => handleCallback(`bypass-${Date.now()}`), 0);
    return () => clearTimeout(t);
  }, [TURNSTILE_DISABLED, handleCallback]);

  // Dev mode: short-circuit with a fake token regardless of enablement
  useEffect(() => {
    if (!USE_DEV_MODE || TURNSTILE_DISABLED) return;
    const timer = setTimeout(() => handleCallback('dev-mode-token-' + Date.now()), 50);
    return () => clearTimeout(timer);
  }, [handleCallback, TURNSTILE_DISABLED]);

  // Real widget path
  useEffect(() => {
    if (TURNSTILE_DISABLED || USE_DEV_MODE) return;

    let mounted = true;
    let retryCount = 0;
    const maxRetries = 10;
    const retryDelay = 300;

    const renderWidget = () => {
      if (!mounted || !ref.current || !window.turnstile) return;

      try {
        // Clean up any existing instance (hot reloads, re-mounts)
        if (widgetIdRef.current) {
          window.turnstile.remove(widgetIdRef.current);
          widgetIdRef.current = null;
        }

        if (!SITE_KEY) {
          setError('Turnstile site key not configured');
          return;
        }

        const options: TurnstileOptions = {
          sitekey: SITE_KEY,
          callback: handleCallback,
          'error-callback': handleError,
          'expired-callback': handleExpired,
          theme,
          size,
        };

        const id = window.turnstile.render(ref.current, options);
        widgetIdRef.current = id;

        if (size === 'invisible' && autoExecute) {
          try { window.turnstile.execute(id); } catch { /* ignore */ }
        }

        setError(null);
      } catch {
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
        try { window.turnstile.remove(widgetIdRef.current); } catch { /* ignore */ }
      }
    };
  }, [TURNSTILE_DISABLED, USE_DEV_MODE, SITE_KEY, handleCallback, handleError, handleExpired, theme, size, autoExecute]);

  // Expose a reset helper (respects dev/bypass/real widget)
  useEffect(() => {
    window.resetTurnstile = () => {
      if (TURNSTILE_DISABLED) {
        handleCallback('bypass-reset-' + Date.now());
        return;
      }
      if (USE_DEV_MODE) {
        handleCallback('dev-mode-token-reset-' + Date.now());
        return;
      }
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.reset(widgetIdRef.current);
          if (size === 'invisible' && autoExecute) window.turnstile.execute(widgetIdRef.current);
        } catch { /* ignore */ }
      }
    };
    return () => { delete window.resetTurnstile; };
  }, [TURNSTILE_DISABLED, USE_DEV_MODE, handleCallback, autoExecute, size]);

  // Render nothing when disabled (bypass), or when in dev-mode (no UI needed).
  if (TURNSTILE_DISABLED || USE_DEV_MODE) return null;

  if (error) {
    return <p className="text-red-600 text-sm mt-2">{error}</p>;
  }

  // When enabled and not dev-mode, render the container for Cloudflare’s script to mount into.
  return <div ref={ref} data-testid="turnstile" />;
};

export default Turnstile;
