// src/pages/DonatePage.tsx
import React from 'react';
import { StaticPage } from './StaticPage';
import { useConfig } from '../context/ConfigContext';

/**
 * Renders the "Donate / Support" page (content from appconfig.local.yaml →
 * content.donatePage) plus a real Ko-fi support button when a `donationUrl` is
 * configured. Degrades to text-only when no URL is set — never a broken link.
 */
export const DonatePage: React.FC = () => {
  const { config } = useConfig();
  const url = (config?.content?.donationUrl ?? '').trim();

  return (
    <StaticPage pageKey="donatePage">
      {url && (
        <div className="not-prose mt-8 flex justify-center">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="donate-page-go"
            className="inline-flex min-h-[44px] items-center justify-center rounded-xl bg-primary px-6 py-2.5 text-sm font-semibold text-white shadow-sm transition-opacity duration-fast hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
          >
            Support Quafel on Ko-fi ☕
          </a>
        </div>
      )}
    </StaticPage>
  );
};

export default DonatePage;
