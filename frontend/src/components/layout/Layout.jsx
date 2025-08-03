import React from 'react';
import { Outlet } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';

/**
 * A dedicated Footer component for better code organization.
 */
const Footer = () => {
  const config = useConfig();
  // Provide fallbacks for config values to prevent errors during loading
  const brandName = config?.content?.brand?.name || 'Quizzical.ai';
  const copyrightText = config?.content?.footer?.copyright || `© ${new Date().getFullYear()}`;

  return (
    <footer className="w-full p-4 text-secondary" role="contentinfo">
      <div className="max-w-5xl mx-auto flex items-center justify-between text-sm">
        <p className="font-bold">{brandName}</p>
        <p>{copyrightText}</p>
      </div>
    </footer>
  );
};

/**
 * The main Layout component for the application.
 * It provides a consistent structure and improves accessibility.
 */
function Layout() {
  return (
    <div className="flex flex-col min-h-screen bg-background text-primary">
      {/* The <main> tag is semantically important for accessibility */}
      <main className="flex-grow" role="main">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}

export default Layout;
