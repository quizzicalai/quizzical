// src/components/layout/Header.jsx
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { Logo } from '../common/Logo';

export function Header() {
  const navigate = useNavigate();
  const { config } = useConfig();
  const appName = config?.content?.appName ?? 'Quizzical';

  const handleLogoClick = () => {
    navigate('/'); // Navigate to landing page, preserving history
  };

  return (
    <header role="banner" className="border-b border-gray-200 bg-background-color">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
        <button
          type="button"
          onClick={handleLogoClick}
          className="flex items-center gap-2 rounded-md focus:outline-none focus:ring-2 focus:ring-primary-color/50"
          aria-label={`Go to ${appName} homepage`}
        >
          <Logo className="h-8 w-8 text-primary-color" />
          <span className="text-xl font-semibold text-fg">{appName}</span>
        </button>
      </div>
    </header>
  );
}