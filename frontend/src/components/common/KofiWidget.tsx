import { useEffect } from 'react';
import { useConfig } from '../../context/ConfigContext';
import { kofiUsername } from './kofiUsername';

declare global {
  interface Window {
    kofiWidgetOverlay?: {
      draw: (username: string, opts: Record<string, string>) => void;
    };
  }
}

const SCRIPT_SRC = 'https://storage.ko-fi.com/cdn/scripts/overlay-widget.js';
const SCRIPT_ID = 'kofi-overlay-widget';
const DRAWN_ATTR = 'data-kofi-drawn';

/**
 * KofiWidget — the site-wide Ko-fi "Donate" floating button.
 *
 * Loaded the CSP-safe way: we inject Ko-fi's EXTERNAL overlay script (allowed
 * via `script-src https://storage.ko-fi.com`) and call `kofiWidgetOverlay.draw`
 * from this bundled module — NOT from an inline <script>, which the app's CSP
 * forbids (`script-src` has no `'unsafe-inline'`). The script loads lazily
 * (requestIdleCallback) so it never blocks first paint, and activates only when
 * a ko-fi.com `donationUrl` is configured (the same signal the DonateCTA uses),
 * degrading to nothing otherwise. Renders no React DOM — Ko-fi injects its own
 * floating button into <body>.
 */
export function KofiWidget() {
  const { config } = useConfig();
  const username = kofiUsername(config?.content?.donationUrl);

  useEffect(() => {
    if (!username) return;
    if (typeof window === 'undefined' || typeof document === 'undefined') return;

    let cancelled = false;

    const draw = (script: HTMLScriptElement | null) => {
      if (cancelled || !window.kofiWidgetOverlay) return;
      // Idempotent across SPA route remounts (the button lives in <body>).
      if (script?.getAttribute(DRAWN_ATTR) === '1') return;
      try {
        window.kofiWidgetOverlay.draw(username, {
          type: 'floating-chat',
          'floating-chat.donateButton.text': 'Donate',
          // Brand-aligned (sea-blue #0079AE + white) vs Ko-fi's default light blue.
          'floating-chat.donateButton.background-color': '#0079AE',
          'floating-chat.donateButton.text-color': '#FFFFFF',
        });
        script?.setAttribute(DRAWN_ATTR, '1');
      } catch {
        /* widget failed to draw — non-critical; footer / result / donate-page links still work */
      }
    };

    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
    if (existing) {
      if (window.kofiWidgetOverlay) draw(existing);
      else existing.addEventListener('load', () => draw(existing), { once: true });
      return () => {
        cancelled = true;
      };
    }

    const load = () => {
      if (cancelled) return;
      const script = document.createElement('script');
      script.id = SCRIPT_ID;
      script.src = SCRIPT_SRC;
      script.async = true;
      script.addEventListener('load', () => draw(script), { once: true });
      document.body.appendChild(script);
    };

    // Load the third-party script off the critical path, with a timeout fallback.
    const hasRIC = typeof window.requestIdleCallback === 'function';
    const handle: number = hasRIC
      ? window.requestIdleCallback(load, { timeout: 3000 })
      : window.setTimeout(load, 1200);

    return () => {
      cancelled = true;
      if (hasRIC && typeof window.cancelIdleCallback === 'function') {
        window.cancelIdleCallback(handle);
      } else {
        window.clearTimeout(handle);
      }
    };
  }, [username]);

  return null;
}

export default KofiWidget;
