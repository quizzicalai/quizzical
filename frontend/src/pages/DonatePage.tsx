// src/pages/DonatePage.tsx
import React from 'react';
import { StaticPage } from './StaticPage';

/**
 * Renders the "Donate / Support" page content using the generic StaticPage component.
 * Content is served from the backend config (appconfig.local.yaml → content.donatePage).
 */
export const DonatePage: React.FC = () => {
  return <StaticPage pageKey="donatePage" />;
};
