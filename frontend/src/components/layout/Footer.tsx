import React, { useState, useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { Logo } from '../../assets/icons/Logo';
import clsx from 'clsx';
import { useCloseOnRouteChange } from '../../hooks/useCloseOnRouteChange';

// ============================================================================
// Types
// ============================================================================

type FooterProps = {
  variant?: 'landing' | 'quiz';
};

type NavLinkProps = {
  link?: { label: string; href: string; external?: boolean };
  className?: string;
  onClick?: () => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  tabIndex?: number;
};

type MenuButtonProps = {
  isOpen: boolean;
  onClick: () => void;
};

// ============================================================================
// Components
// ============================================================================

/**
 * A reusable, accessible link component for the footer.
 */
const NavLink: React.FC<NavLinkProps> = ({ 
  link, 
  className, 
  onClick, 
  onKeyDown,
  tabIndex = 0 
}) => {
  if (!link?.href || !link?.label) return null;

  const commonProps = {
    className: clsx(
      'block text-sm text-muted hover:text-fg transition-colors',
      'focus:outline-none focus:ring-2 focus:ring-primary/50 focus:rounded',
      className
    ),
    onClick,
    onKeyDown,
    tabIndex,
    children: link.label,
  };

  if (link.external) {
    return (
      <a 
        href={link.href} 
        target="_blank" 
        rel="noopener noreferrer"
        aria-label={`${link.label} (opens in new tab)`}
        {...commonProps} 
      />
    );
  }

  return <Link to={link.href} {...commonProps} />;
};

/**
 * Accessible mobile menu button, created with forwardRef to correctly handle the ref.
 */
const MenuButton = React.forwardRef<HTMLButtonElement, MenuButtonProps>(
  ({ isOpen, onClick }, ref) => {
    const label = isOpen ? 'Close navigation menu' : 'Open navigation menu';
    
    return (
      <button
        ref={ref}
        type="button"
        onClick={onClick}
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-controls="footer-mobile-menu"
        aria-label={label}
        className={clsx(
          'p-2 rounded-md text-muted transition-all duration-200',
          'hover:bg-gray-100 dark:hover:bg-gray-800',
          'focus:outline-none focus:ring-2 focus:ring-primary/50',
          isOpen && 'bg-gray-100 dark:bg-gray-800 rotate-90'
        )}
      >
        {isOpen ? (
          // Close icon (X)
          <svg 
            xmlns="http://www.w3.org/2000/svg" 
            className="h-5 w-5 transition-transform" 
            viewBox="0 0 20 20" 
            fill="currentColor"
            aria-hidden="true"
          >
            <path 
              fillRule="evenodd" 
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" 
              clipRule="evenodd" 
            />
          </svg>
        ) : (
          // Menu icon (three dots)
          <svg 
            xmlns="http://www.w3.org/2000/svg" 
            className="h-5 w-5" 
            viewBox="0 0 20 20" 
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" />
          </svg>
        )}
      </button>
    );
  }
);
MenuButton.displayName = 'MenuButton'; // Good practice for debugging with forwardRef

// ============================================================================
// Main Component
// ============================================================================

export const Footer: React.FC<FooterProps> = ({ variant = 'landing' }) => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  
  const menuRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuNavRef = useRef<HTMLElement>(null);

  useCloseOnRouteChange(() => setIsMenuOpen(false));

  // Close menu on outside click
  useEffect(() => {
    if (!isMenuOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsMenuOpen(false);
      }
    };

    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 0);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isMenuOpen]);

  // Keyboard navigation and escape handling
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!isMenuOpen) return;

      if (event.key === 'Escape') {
        event.preventDefault();
        setIsMenuOpen(false);
        toggleRef.current?.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isMenuOpen]);

  // Focus first menu item when menu opens
  useEffect(() => {
    if (isMenuOpen && menuNavRef.current) {
      const firstFocusable = menuNavRef.current.querySelector<HTMLElement>(
        'a, button, [tabindex]:not([tabindex="-1"])'
      );
      setTimeout(() => firstFocusable?.focus(), 50);
    }
  }, [isMenuOpen]);

  if (!config) return null;

  const links = config.content.footer;
  const copyright = links?.copyright ?? 'Quizzical.ai';
  const year = new Date().getFullYear();
  const hasDivider = links.about && links.donate && (links.terms || links.privacy);

  const handleLinkClick = () => setIsMenuOpen(false);

  const handleLinkKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      handleLinkClick();
    }
  };

  return (
    <footer 
      role="contentinfo" 
      className="border-t bg-bg mt-auto"
      aria-label="Site footer"
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4">
        <div className="flex items-center gap-3">
          {variant !== 'landing' && (
            <button
              type="button"
              onClick={() => navigate('/')}
              aria-label="Go to homepage"
              className={clsx(
                'mr-2 rounded-full transition-transform hover:scale-110',
                'focus:outline-none focus:ring-2 focus:ring-primary/50'
              )}
            >
              <Logo className="h-6 w-6 text-muted hover:text-primary transition-colors" />
            </button>
          )}
          <span className="text-xs text-muted">
            © {year} {copyright}
          </span>
        </div>

        <nav 
          className="hidden sm:flex items-center gap-4" 
          aria-label="Footer navigation"
        >
          <NavLink link={links.about} />
          <NavLink link={links.terms} />
          <NavLink link={links.privacy} />
          <NavLink link={links.donate} />
        </nav>

        <div className="sm:hidden">
          <div className="relative" ref={menuRef}>
            <MenuButton 
              ref={toggleRef} // Pass ref directly using the 'ref' prop
              isOpen={isMenuOpen}
              onClick={() => setIsMenuOpen(!isMenuOpen)}
            />
            
            {isMenuOpen && (
              <>
                <div role="status" aria-live="polite" className="sr-only">
                  Navigation menu opened.
                </div>
                <nav
                  ref={menuNavRef}
                  id="footer-mobile-menu"
                  className="absolute right-0 bottom-full mb-2 w-48 bg-bg border border-border rounded-md shadow-lg z-10"
                  aria-label="Footer navigation menu"
                >
                  <ul className="p-2 space-y-1" role="list">
                    {links.about && (
                      <li role="none">
                        <NavLink link={links.about} className="px-2 py-1 w-full text-left hover:bg-gray-100 dark:hover:bg-gray-800 rounded" onClick={handleLinkClick} onKeyDown={handleLinkKeyDown} tabIndex={0}/>
                      </li>
                    )}
                    {links.donate && (
                      <li role="none">
                        <NavLink link={links.donate} className="px-2 py-1 w-full text-left hover:bg-gray-100 dark:hover:bg-gray-800 rounded" onClick={handleLinkClick} onKeyDown={handleLinkKeyDown} tabIndex={0}/>
                      </li>
                    )}
                    
                    {hasDivider && (
                      <li role="separator" className="border-t border-border my-1" />
                    )}

                    {links.terms && (
                      <li role="none">
                        <NavLink link={links.terms} className="px-2 py-1 w-full text-left hover:bg-gray-100 dark:hover:bg-gray-800 rounded" onClick={handleLinkClick} onKeyDown={handleLinkKeyDown} tabIndex={0}/>
                      </li>
                    )}
                    {links.privacy && (
                      <li role="none">
                        <NavLink link={links.privacy} className="px-2 py-1 w-full text-left hover:bg-gray-100 dark:hover:bg-gray-800 rounded" onClick={handleLinkClick} onKeyDown={handleLinkKeyDown} tabIndex={0}/>
                      </li>
                    )}
                  </ul>
                </nav>
              </>
            )}
          </div>
        </div>
      </div>
    </footer>
  );
};