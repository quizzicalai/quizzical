import React, { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

/**
 * AC-FE-A11Y-FOCUS-1..3: Route-change focus management + screen-reader
 * announcement.
 *
 * On every route change:
 *  - Sets focus on the canonical `<main id="main-content">` (so the next Tab
 *    starts inside the new page, not stuck in the previous Header).
 *  - Updates a visually hidden `aria-live="polite"` region with a
 *    human-friendly route name so screen readers announce the navigation.
 *
 * The first render is intentionally skipped so we don't steal initial focus
 * from autofocus inputs.
 */

const ROUTE_NAMES: Record<string, string> = {
  '/': 'Home',
  '/about': 'About',
  '/terms': 'Terms of Service',
  '/privacy': 'Privacy Policy',
  '/help': 'Help',
};

function describeRoute(pathname: string): string {
  if (ROUTE_NAMES[pathname]) return ROUTE_NAMES[pathname];
  if (pathname.startsWith('/quiz')) return 'Quiz';
  if (pathname.startsWith('/result')) return 'Quiz Result';
  if (pathname === '/error') return 'Error';
  // Fall back to a non-empty announcement so SR users still hear something.
  return 'Page';
}

export const RouteAnnouncer: React.FC = () => {
  const location = useLocation();
  const liveRef = useRef<HTMLDivElement>(null);
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    const label = `Navigated to ${describeRoute(location.pathname)}`;
    if (liveRef.current) {
      // aria-atomic="true" + textContent change is enough to re-trigger
      // SR announcement on every navigation, including same label twice.
      liveRef.current.textContent = label;
    }

    // Move focus to <main id="main-content"> so keyboard users land in the
    // new page's content. tabIndex=-1 makes it programmatically focusable
    // without becoming part of the tab order.
    const main = document.getElementById('main-content');
    if (main && typeof main.focus === 'function') {
      try {
        main.focus({ preventScroll: true });
      } catch {
        main.focus();
      }
    }
  }, [location.pathname]);

  return (
    <div
      ref={liveRef}
      aria-live="polite"
      aria-atomic="true"
      role="status"
      className="sr-only"
      data-testid="route-announcer"
    />
  );
};

export default RouteAnnouncer;
