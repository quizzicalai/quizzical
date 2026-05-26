// src/components/layout/Header.tsx
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';

export const Header: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const appName = config?.content?.appName ?? 'Quafel';
  // AC-UX-2026-05-09 — brand wordmark now carries the long-form tagline
  // to clarify product purpose at a glance. The tagline is visible on
  // sm+ screens and collapses to just the appName on phones to preserve
  // header height.
  const tagline = 'The Personality Quiz for Everything';

  const handleLogoClick = () => {
    navigate('/'); // Navigate to landing page, preserving history
  };

  return (
    // AC-UX-2026-05-10 — sticky header with translucent bg + backdrop
    // blur so it stays legible over scrolled content on the quiz/result
    // pages without competing with the content underneath.
    <header
      role="banner"
      className="sticky top-0 z-30 bg-bg/85 backdrop-blur supports-[backdrop-filter]:bg-bg/70"
    >
      <div className="mx-auto flex h-12 max-w-7xl items-center justify-between px-4 sm:px-6">
        <button
          type="button"
          onClick={handleLogoClick}
          data-testid="header-wordmark"
          className="-mx-2 inline-flex min-h-[44px] cursor-pointer items-center gap-2 rounded-md px-2 transition-colors hover:bg-card focus:outline-none focus:ring-2 focus:ring-primary/50"
          aria-label={`Go to ${appName} homepage`}
        >
          <span className="text-[14px] font-semibold tracking-tight text-fg">{appName}</span>
          <span
            aria-hidden="true"
            className="hidden sm:inline text-[12px] font-normal tracking-tight text-muted"
          >
            — {tagline}
          </span>
        </button>
      </div>
    </header>
  );
};