// src/components/layout/Footer.jsx
import React, { useState, useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { Logo } from '../common/Logo';
import clsx from 'clsx';

export function Footer({ variant = 'landing' }) {
  const navigate = useNavigate();
  const { config } = useConfig();
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const menuRef = useRef(null);

  const links = config?.content?.footer;
  const copyright = links?.copyright ?? 'Quizzical';
  const year = new Date().getFullYear();

  // Close menu if clicking outside of it
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setIsMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);


  const NavLink = ({ itemKey, className }) => {
    const item = links?.[itemKey];
    if (!item?.href || !item?.label) return null;

    const Component = item.external ? 'a' : Link;
    const props = item.external
      ? { href: item.href, target: '_blank', rel: 'noopener noreferrer' }
      : { to: item.href };

    return (
      <Component {...props} className={clsx('block sm:inline-block text-sm text-muted hover:text-fg', className)}>
        {item.label}
      </Component>
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

        {/* Desktop Links */}
        <nav className="hidden sm:flex items-center gap-4" aria-label="Footer navigation">
          <NavLink itemKey="about" />
          <NavLink itemKey="terms" />
          <NavLink itemKey="privacy" />
          <NavLink itemKey="donate" />
        </nav>

        {/* Mobile Links & Menu */}
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
              <div
                className="absolute right-0 bottom-full mb-2 w-48 bg-bg border border-border rounded-md shadow-lg z-10"
                role="menu"
              >
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
}