// src/components/layout/Layout.tsx
import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Header } from './Header';
import { Footer } from './Footer';

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
    <div className="flex flex-col min-h-screen bg-bg text-fg">
      <Header />
      <main className="flex-grow" role="main">
        <Outlet /> {/* Child routes from AppRouter will render here */}
      </main>
      <Footer variant={footerVariant} />
    </div>
  );
};