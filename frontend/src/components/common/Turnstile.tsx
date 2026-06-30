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
  const { features, isLoading: configLoading } = useConfig();

  // Final policy:
  // - If backend says disabled -> bypass (emit token, render nothing)
  // - Else use a real widget; require a site key from /config or Vite fallback
  const TURNSTILE_DISABLED = features.turnstile === false;
  const SITE_KEY = (features.turnstileSiteKey ?? FALLBACK_SITE_KEY).trim();
  // #16 (HITLIST-2026-06-30 review) — in prod the site key arrives ONLY from
  // the BACKGROUND /config reconcile (DEFAULT_APP_CONFIG carries none). While
  // that reconcile is still in flight (configLoading), a missing key is
  // EXPECTED to be transient — show a quiet "preparing" state instead of the
  // cryptic "site key not configured" error, which would otherwise flash on
  // the landing page during the brief pre-reconcile window. The bounded
  // auto-retry in ConfigProvider lands the key shortly after first paint.
  const siteKeyPending = !SITE_KEY && configLoading && !TURNSTILE_DISABLED;

  const ref = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bounds the automatic reset+re-execute self-heal on `error-callback` so a
  // persistent Cloudflare-level failure can't loop forever. Reset whenever a
  // good token arrives (see handleCallback).
  const errorRecoveryCountRef = useRef(0);
  const MAX_ERROR_RECOVERIES = 2;

  const handleCallback = useCallback(
    (token: string) => {
      // A good token arrived — clear any prior error and reset the
      // self-heal budget so a future transient error can recover again.
      setError(null);
      errorRecoveryCountRef.current = 0;
      onVerify(token);
    },
    [onVerify]
  );

  const handleError = useCallback(() => {
    onError?.();
    // QUIZ-UX-POLISH item 3 — Cloudflare's invisible widget commonly fires
    // `error-callback` on the FIRST execute attempt (esp. on mobile:
    // transient network / interactive-challenge race, codes 300xxx/600xxx).
    // Once a widget instance has errored it will NOT mint a token on its own
    // — it must be reset() then execute()'d again. Previously we only set an
    // error message and left the widget stuck with no token, so the first
    // attempt spuriously "failed" and the user had to reload / click again
    // (a fresh mount) to succeed. We now transparently reset + re-execute
    // (invisible + autoExecute) a bounded number of times so the first
    // attempt self-recovers; only after exhausting the budget do we surface
    // the visible "Verification failed" message.
    if (
      widgetIdRef.current &&
      window.turnstile &&
      size === 'invisible' &&
      autoExecute &&
      errorRecoveryCountRef.current < MAX_ERROR_RECOVERIES
    ) {
      errorRecoveryCountRef.current += 1;
      try {
        window.turnstile.reset(widgetIdRef.current);
        window.turnstile.execute(widgetIdRef.current);
        // Self-heal in progress — don't flash a failure message yet.
        return;
      } catch {
        /* fall through to surfacing the error */
      }
    }
    setError('Verification failed. Please try again.');
  }, [onError, autoExecute, size]);

  const handleExpired = useCallback(() => {
    onExpire?.();
    // QUIZ-UX-POLISH item 3 — Cloudflare requires reset() BEFORE re-execute()
    // after expiry; calling execute() on an expired widget without resetting
    // first can return `timeout-or-duplicate` and never mint a fresh token.
    if (widgetIdRef.current && window.turnstile && size === 'invisible' && autoExecute) {
      try {
        window.turnstile.reset(widgetIdRef.current);
        window.turnstile.execute(widgetIdRef.current);
      } catch { /* ignore */ }
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
          // #16 — while the /config reconcile is still in flight the key is
          // expected to arrive shortly; stay quiet (no error flash). This
          // effect re-runs when SITE_KEY changes (it's in the dep list), so the
          // widget renders as soon as the reconcile lands the key. Only surface
          // the hard error once the reconcile has settled without a key.
          if (!siteKeyPending) {
            setError('Turnstile site key not configured');
          }
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
  }, [TURNSTILE_DISABLED, SITE_KEY, siteKeyPending, handleCallback, handleError, handleExpired, theme, size, autoExecute]);

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
  }, [TURNSTILE_DISABLED, handleCallback, autoExecute, size]);

  // Render nothing when disabled (bypass), or when in dev-mode (no UI needed).
  if (TURNSTILE_DISABLED || USE_DEV_MODE) return null;

  // #16 — while the /config reconcile is still bringing the site key, suppress
  // any error surface (incl. a stale one) and keep the invisible container
  // mounted so the widget can render the instant the key lands.
  if (error && !siteKeyPending) {
    return <p className="text-error text-sm mt-2">{error}</p>;
  }

  // When enabled and not dev-mode, render the container for Cloudflare’s script to mount into.
  return <div ref={ref} data-testid="turnstile" />;
};

export default Turnstile;
