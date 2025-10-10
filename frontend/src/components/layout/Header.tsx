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
    <header role="banner" className="bg-bg">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
        <button
          type="button"
          onClick={handleLogoClick}
          className="flex items-center gap-2 rounded-md focus:outline-none focus:ring-2 focus:ring-primary/50"
          aria-label={`Go to ${appName} homepage`}
        >
          {/* Wizard Cat logo */}
          <WizardCatIcon className="h-8 w-auto text-primary" strokeWidth={2} />
          <span className="text-xl font-semibold text-fg">{appName}</span>
        </button>
      </div>
    </header>
  );
};