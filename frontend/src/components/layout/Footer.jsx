// src/components/layout/Footer.jsx
import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { Logo } from '../common/Logo';
import clsx from 'clsx';

export function Footer({ variant = 'landing' }) {
  const navigate = useNavigate();
  const { config } = useConfig();
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  const links = config?.content?.footer ?? {};
  const copyright = links.copyright ?? 'Quizzical';
  const year = new Date().getFullYear();

  const NavLink = ({ itemKey, className }) => {
    const item = links[itemKey];
    if (!item?.href || !item?.label) return null;
    const isExternal = item.href.startsWith('http');
    
    const Component = isExternal ? 'a' : Link;
    const props = isExternal
      ? { href: item.href, target: '_blank', rel: 'noopener noreferrer' }
      : { to: item.href };

    return (
      <Component {...props} className={clsx('text-sm text-muted hover:text-fg', className)}>
        {item.label}
      </Component>
    );
  };

  return (
    <footer role="contentinfo" className="border-t bg-background-color mt-auto">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4">
        <div className="flex items-center gap-3">
          {variant !== 'landing' && (
            <button
              type="button"
              onClick={() => navigate('/')}
              aria-label="Go to homepage"
              className="mr-2"
            >
              <Logo className="h-6 w-6 text-muted hover:text-primary-color" />
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
          <div className="relative">
            <button
              type="button"
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              aria-haspopup="true"
              aria-expanded={isMenuOpen}
              className="p-2 rounded-md text-muted hover:bg-gray-100"
            >
              <span className="sr-only">Open menu</span>
              •••
            </button>
            {isMenuOpen && (
              <div
                className="absolute right-0 bottom-full mb-2 w-48 bg-white border rounded-md shadow-lg z-10"
                role="menu"
              >
                <div className="p-2 space-y-2">
                  <NavLink itemKey="about" className="block" />
                  <NavLink itemKey="donate" className="block" />
                  <hr/>
                  <NavLink itemKey="terms" className="block" />
                  <NavLink itemKey="privacy" className="block" />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </footer>
  );
}