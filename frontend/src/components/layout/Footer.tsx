// src/components/layout/Footer.tsx
import React, { useState, useEffect, useRef } from 'react';
import { Link, useNavigate, LinkProps } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { Logo } from '../../assets/icons/Logo';
import clsx from 'clsx';
import { FooterConfig, FooterLink } from '../../types/config';

type FooterProps = {
  variant?: 'landing' | 'quiz';
};

// Define the keys for the links here, outside the component, so the type below can use them.
const linkKeys = ["about", "terms", "privacy", "donate"] as const;

type NavLinkProps = {
  // Use the keys we defined above.
  itemKey: typeof linkKeys[number];
  className?: string;
};

export const Footer: React.FC<FooterProps> = ({ variant = 'landing' }) => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const links = config?.content?.footer;
  const copyright = links?.copyright ?? 'Quizzical.ai';
  const year = new Date().getFullYear();

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      // FIX 2: Correct the typo from menu-ref to menuRef
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

  const NavLink: React.FC<NavLinkProps> = ({ itemKey, className }) => {
    const item = links?.[itemKey] as FooterLink | undefined;
    if (!item?.href || !item?.label) return null;

    // FIX 3: We create specific props for each component type
    // and use a type assertion to satisfy TypeScript.
    const commonProps = {
      className: clsx('block sm:inline-block text-sm text-muted hover:text-fg', className),
      children: item.label,
    };

    if (item.external) {
      return (
        <a
          href={item.href}
          target="_blank"
          rel="noopener noreferrer"
          {...commonProps}
        >
          {item.label}
        </a>
      );
    }

    return (
      <Link to={item.href} {...commonProps}>
        {item.label}
      </Link>
    );
  };

  return (
    <footer role="contentinfo" className="border-t bg-bg mt-auto">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4">
        <div className="flex items-center gap-3">
          {variant !== 'landing' && (
            <button
              type="button"
              onClick={() => navigate('/')}
              aria-label="Go to homepage"
              className="mr-2 rounded-full focus:outline-none focus:ring-2 focus:ring-primary/50"
            >
              <Logo className="h-6 w-6 text-muted hover:text-primary" />
            </button>
          )}
          <span className="text-xs text-muted">{`© ${year} ${copyright}`}</span>
        </div>

        <nav className="hidden sm:flex items-center gap-4" aria-label="Footer navigation">
          <NavLink itemKey="about" />
          <NavLink itemKey="terms" />
          <NavLink itemKey="privacy" />
          <NavLink itemKey="donate" />
        </nav>

        <div className="sm:hidden">
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              aria-haspopup="true"
              aria-expanded={isMenuOpen}
              className="p-2 rounded-md text-muted hover:bg-gray-100 dark:hover:bg-gray-800"
              aria-label="Open menu"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" /></svg>
            </button>
            {isMenuOpen && (
              <div className="absolute right-0 bottom-full mb-2 w-48 bg-bg border border-border rounded-md shadow-lg z-10" role="menu">
                <div className="p-2 space-y-1">
                  <NavLink itemKey="about" className="px-2 py-1" />
                  <NavLink itemKey="donate" className="px-2 py-1" />
                  <div className="border-t border-border my-1"></div>
                  <NavLink itemKey="terms" className="px-2 py-1" />
                  <NavLink itemKey="privacy" className="px-2 py-1" />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </footer>
  );
};