// src/components/layout/Header.tsx
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';

export const Header: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const appName = config?.content?.appName ?? 'Quizzical.ai';

  const handleLogoClick = () => {
    navigate('/'); // Navigate to landing page, preserving history
  };

  return (
    <header
      role="banner"
      className="sticky top-0 z-30 bg-bg"
    >
      <div className="mx-auto flex h-10 max-w-7xl items-center justify-between px-4 sm:px-6">
        <button
          type="button"
          onClick={handleLogoClick}
          data-testid="header-wordmark"
          className="-mx-2 inline-flex min-h-[40px] cursor-pointer items-center rounded-md px-2 transition-colors hover:bg-card/70 focus:outline-none focus:ring-2 focus:ring-primary/50"
          aria-label={`Go to ${appName} homepage`}
        >
          <span className="text-[14px] font-semibold tracking-tight text-fg">{appName}</span>
        </button>
      </div>
    </header>
  );
};