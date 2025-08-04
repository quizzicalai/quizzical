// src/pages/AboutPage.tsx
import React from 'react';
import { StaticPage } from './StaticPage';

/**
 * Renders the "About" page content using the generic StaticPage component.
 * This ensures consistency with other static content pages like Terms and Privacy.
 */
export const AboutPage: React.FC = () => {
  return <StaticPage pageKey="aboutPage" />;
};
