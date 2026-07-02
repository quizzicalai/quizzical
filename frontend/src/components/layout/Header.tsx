// src/components/layout/Header.tsx
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
// Vite asset import — resolves to the emitted URL of the sea-blue "q" mark.
import quafelLogoUrl from '../../assets/logo/quafel_logo_indigo.png';

export const Header: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const appName = config?.content?.appName ?? 'Quafel';
  // AC-UX-2026-05-09 — brand wordmark now carries the long-form tagline
  // to clarify product purpose at a glance. The tagline is visible on
  // sm+ screens and collapses to just the appName on phones to preserve
  // header height.
  const tagline = 'The Personality Quiz for Everything';

  // UI-LOGO-2026-06-29 — header scroll-collapse.
  // At the top of the page we show the full brand lockup (logo + appName +
  // tagline). Once the user scrolls past a tiny sentinel placed at the very
  // top, we smoothly collapse to JUST the logo. An IntersectionObserver on
  // the sentinel is cheaper and jank-free vs. a scroll listener.
  //
  // `collapsed` defaults to false (full lockup) so environments without
  // IntersectionObserver (jsdom/SSR) render the expanded "at top" state.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || typeof IntersectionObserver === 'undefined') return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        // Sentinel visible → page is at the top → expanded.
        // Sentinel scrolled out → collapse to the logo only.
        setCollapsed(!entry.isIntersecting);
      },
      { rootMargin: '0px', threshold: 0 }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []);

  const handleLogoClick = () => {
    navigate('/'); // Navigate to landing page, preserving history
  };

  return (
    <>
      {/* Zero-height sentinel at the very top of the document. When it
          scrolls out of view the header collapses. Rendered outside the
          sticky header so it tracks the document scroll position. */}
      <div
        ref={sentinelRef}
        aria-hidden="true"
        data-testid="header-scroll-sentinel"
        className="absolute left-0 top-0 h-px w-px"
      />
      {/* AC-UX-2026-05-10 — sticky header with translucent bg + backdrop
          blur so it stays legible over scrolled content on the quiz/result
          pages without competing with the content underneath. */}
      <header
        role="banner"
        data-collapsed={collapsed ? 'true' : 'false'}
        className="sticky top-0 z-30 bg-bg/85 backdrop-blur supports-[backdrop-filter]:bg-bg/70"
      >
        <div className="mx-auto flex h-12 max-w-7xl items-center justify-between px-4 sm:px-6">
          <button
            type="button"
            onClick={handleLogoClick}
            data-testid="header-wordmark"
            className="-mx-2 inline-flex min-h-[44px] cursor-pointer items-center gap-2 rounded-md px-2 transition-colors duration-fast ease-out-token hover:bg-card focus:outline-none focus:ring-2 focus:ring-primary/50"
            aria-label={`Go to ${appName} homepage`}
            title={`Go to ${appName} homepage`}
          >
            {/* UI-LOGO-2026-06-29 — the logo mark is always present in the
                header. It is the sole brand element once collapsed. */}
            <img
              src={quafelLogoUrl}
              alt={`${appName} logo`}
              width={28}
              height={28}
              className="h-7 w-7 shrink-0 select-none"
              draggable={false}
            />
            {/* Brand title + tagline lockup. Visible at the top of the page;
                smoothly collapses (fade + width) to just the logo once the
                user scrolls. The transition respects prefers-reduced-motion
                via the global `*` rule in index.css (no spinner-style
                always-animate exemption applies here). */}
            <span
              className={[
                'inline-flex items-center gap-2 overflow-hidden whitespace-nowrap',
                'transition-[max-width,opacity,margin] duration-base ease-out-token',
                collapsed
                  ? 'pointer-events-none ml-0 max-w-0 opacity-0'
                  : 'ml-0 max-w-[80vw] opacity-100',
              ].join(' ')}
            >
              <span className="text-sm font-semibold tracking-tight text-fg">{appName}</span>
              <span
                aria-hidden="true"
                className="hidden sm:inline text-xs font-normal tracking-tight text-muted"
              >
                — {tagline}
              </span>
            </span>
          </button>
        </div>
      </header>
    </>
  );
};
