// src/components/layout/Header.tsx
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { WizardCatIcon } from '../../assets/icons/WizardCatIcon';

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
      className="sticky top-0 z-30 border-b border-border/50 bg-bg/82 backdrop-blur-md"
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2.5">
        <button
          type="button"
          onClick={handleLogoClick}
          className="flex min-h-[44px] cursor-pointer items-center gap-2.5 rounded-xl px-2 py-1.5 transition-colors hover:bg-card/85 focus:outline-none focus:ring-2 focus:ring-primary/50"
          aria-label={`Go to ${appName} homepage`}
        >
          {/* Wizard Cat logo */}
          <WizardCatIcon className="h-8 w-auto text-primary" strokeWidth={2} />
          <span className="text-base font-semibold tracking-tight text-fg sm:text-lg">{appName}</span>
        </button>
      </div>
    </header>
  );
};