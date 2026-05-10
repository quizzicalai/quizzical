import React from 'react';

/**
 * AC-FE-A11Y-LANDMARK-1: A "Skip to main content" link rendered as the first
 * focusable element in the layout. Visually hidden until focused, it lets
 * keyboard and screen-reader users bypass the Header/nav and jump straight to
 * the page content (WCAG 2.4.1 Level A — Bypass Blocks).
 *
 * The target must have id="main-content" (provided by Layout's <main>).
 */
export const SkipLink: React.FC = () => (
  <a
    href="#main-content"
    className={[
      // Visually hidden by default
      'sr-only',
      // Becomes a real, keyboard-focusable button-style link on focus
      'focus:not-sr-only',
      'focus:fixed',
      'focus:top-2',
      'focus:left-2',
      'focus:z-50',
      'focus:px-4',
      'focus:py-2',
      'focus:rounded-md',
      'focus:bg-fg',
      'focus:text-bg',
      'focus:outline-none',
      'focus:ring-2',
      'focus:ring-offset-2',
      'focus:ring-primary',
    ].join(' ')}
  >
    Skip to main content
  </a>
);

export default SkipLink;
