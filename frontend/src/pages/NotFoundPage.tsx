import React from 'react';
import { Link } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';

/**
 * A robust 404 page that gracefully handles missing configuration.
 */
function NotFoundPage() {
  const config = useConfig();
  
  // Safely access config with fallbacks for all text content.
  const heading = config?.content?.notFoundPage?.heading || 'Page Not Found';
  const subheading = config?.content?.notFoundPage?.subheading || "Sorry, we couldn't find the page you're looking for.";
  const buttonText = config?.content?.notFoundPage?.buttonText || 'Go Back Home';

  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-8">
      <h1 className="text-6xl font-extrabold text-accent mb-4">404</h1>
      <h2 className="text-2xl font-bold text-primary mb-2">
        {heading}
      </h2>
      <p className="text-secondary mb-8 max-w-sm">
        {subheading}
      </p>
      <Link
        to="/"
        className="px-6 py-3 bg-primary text-white font-bold rounded-full hover:bg-accent focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2"
      >
        {buttonText}
      </Link>
    </div>
  );
}

export default NotFoundPage;
