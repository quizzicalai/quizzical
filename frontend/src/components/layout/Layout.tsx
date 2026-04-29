// src/components/layout/Layout.tsx
import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Header } from './Header';
import { Footer } from './Footer';
import { SkipLink } from '../common/SkipLink';
import { RouteAnnouncer } from '../common/RouteAnnouncer';

/**
 * The main Layout component for the application.
 * It provides a consistent structure with a Header and Footer,
 * and renders the active route's content via <Outlet>.
 */
export const Layout: React.FC = () => {
  const location = useLocation();
  const isLandingPage = location.pathname === '/';

  // The footer variant changes based on whether we are on the landing page or not.
  const footerVariant = isLandingPage ? 'landing' : 'quiz';

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      {/* AC-FE-A11Y-LANDMARK-1: Skip link is the first focusable element. */}
      <SkipLink />
      {/* AC-FE-A11Y-FOCUS-1..3: announce route changes + focus main. */}
      <RouteAnnouncer />
      <Header />
      {/* AC-FE-A11Y-LANDMARK-2/3: single canonical <main> landmark.
          Pages MUST NOT render their own <main> — wrap content in <div>/<section>. */}
      <main
        id="main-content"
        tabIndex={-1}
        className="flex-grow"
        role="main"
      >
        <Outlet />
      </main>
      <Footer variant={footerVariant} />
    </div>
  );
};