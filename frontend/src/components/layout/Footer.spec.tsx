/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// ---------- Mocks we control ----------
let __config: any = null;
const setConfig = (cfg: any) => (__config = cfg);

// useConfig mock
vi.mock('../../context/ConfigContext', () => ({
  useConfig: () => ({ config: __config }),
  __setConfig: (c: any) => setConfig(c),
}));

// Capture the route-change closer
let __onRouteChangeClose: (() => void) | null = null;
vi.mock('../../hooks/useCloseOnRouteChange', () => ({
  useCloseOnRouteChange: (cb: () => void) => {
    __onRouteChangeClose = cb;
  },
}));

// Mock Logo to a tiny svg
vi.mock('../../assets/icons/Logo', () => ({
  Logo: (props: any) => <svg data-testid="logo" {...props} />,
}));

// Partially mock react-router-dom: keep actual Link, Router, but stub useNavigate
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  };
});

// Subject under test
import { Footer } from './Footer';

const BASE_CONFIG = {
  content: {
    footer: {
      about:   { label: 'About',   href: '/about' },
      terms:   { label: 'Terms',   href: '/terms' },
      privacy: { label: 'Privacy', href: '/privacy' },
      donate:  { label: 'Donate',  href: 'https://donate.example.com', external: true },
      copyright: 'Quizzical AI',
    },
  },
};

function renderFooter(variant: 'landing' | 'quiz' = 'landing') {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Footer variant={variant} />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  cleanup();
  navigateSpy.mockReset();
  __onRouteChangeClose = null;
  setConfig(BASE_CONFIG);
});

afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  cleanup();
});

// --------------------------------------------------------------------------------
// Tests
// --------------------------------------------------------------------------------

describe('Footer', () => {
  it('renders copyright and desktop nav links', () => {
    renderFooter('landing');

    // Copyright
    const year = new Date().getFullYear();
    expect(
      screen.getByText(new RegExp(`©\\s+${year}\\s+Quizzical AI`))
    ).toBeInTheDocument();

    // Desktop nav items exist in the DOM (they’re just hidden by CSS on small screens)
    expect(screen.getAllByRole('link', { name: 'About' })[0]).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Terms' })[0]).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Privacy' })[0]).toBeInTheDocument();
    // External link shows as anchor with target and rel and proper aria-label
    const donate = screen.getByRole('link', { name: /Donate .*opens in new tab/i });
    expect(donate).toHaveAttribute('href', 'https://donate.example.com');
    expect(donate).toHaveAttribute('target', '_blank');
    expect(donate).toHaveAttribute('rel', expect.stringMatching(/noopener/i));
  });

  it('shows the logo button only for variant="quiz" and navigates home when clicked', () => {
    renderFooter('landing');
    expect(screen.queryByTestId('logo')).toBeNull();

    cleanup();
    renderFooter('quiz');

    const logoBtn = screen.getByRole('button', { name: /go to homepage/i });
    expect(logoBtn).toBeInTheDocument();
    fireEvent.click(logoBtn);
    expect(navigateSpy).toHaveBeenCalledWith('/');
  });

  it('mobile menu toggles open/close via button and closes when a link is clicked', () => {
    renderFooter('landing');

    // Closed initially; menu button says "Open..."
    const openBtn = screen.getByRole('button', { name: /open navigation menu/i });
    expect(openBtn).toBeInTheDocument();

    // Open it
    fireEvent.click(openBtn);
    // now aria-expanded true & label changes
    expect(openBtn).toHaveAttribute('aria-expanded', 'true');
    expect(openBtn).toHaveAccessibleName(/close navigation menu/i);

    // Live region announces
    expect(screen.getByRole('status')).toHaveTextContent(/navigation menu opened/i);

    // Menu is rendered with links
    const menu = screen.getByRole('navigation', { name: /footer navigation menu/i });
    expect(menu).toBeInTheDocument();

    // Click a menu link (About); should close
    const aboutLink = screen.getAllByRole('link', { name: 'About' }).find(el =>
      el.closest('#footer-mobile-menu')
    )!;
    fireEvent.click(aboutLink);
    expect(openBtn).toHaveAttribute('aria-expanded', 'false');
    // menu removed
    expect(screen.queryByRole('navigation', { name: /footer navigation menu/i })).toBeNull();
  });

  it('closes menu on Escape and restores focus to the toggle', () => {
    renderFooter('landing');

    const toggle = screen.getByRole('button', { name: /open navigation menu/i });
    fireEvent.click(toggle); // open

    // ESC closes and re-focuses toggle
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(document.activeElement).toBe(toggle);
  });

  it('closes menu on outside click', () => {
    renderFooter('landing');

    const toggle = screen.getByRole('button', { name: /open navigation menu/i });
    fireEvent.click(toggle); // open

    // The outside-click listener adds after a setTimeout(0)
    act(() => vi.advanceTimersByTime(1));

    // Fire a click outside the menu container
    fireEvent.mouseDown(document.body);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('navigation', { name: /footer navigation menu/i })).toBeNull();
  });

  it('focuses first focusable menu item after opening (with a slight delay)', () => {
    renderFooter('landing');

    const toggle = screen.getByRole('button', { name: /open navigation menu/i });
    fireEvent.click(toggle); // open

    // focusing first menu item happens after setTimeout(50)
    act(() => vi.advanceTimersByTime(50));

    const firstMenuLink = screen
      .getAllByRole('link')
      .find(el => el.closest('#footer-mobile-menu'));
    expect(firstMenuLink).toBeTruthy();
    expect(document.activeElement).toBe(firstMenuLink!);
  });

  it('closes when useCloseOnRouteChange callback fires', async () => {
    renderFooter('landing');

    const toggle = screen.getByRole('button', { name: /open navigation menu/i });
    fireEvent.click(toggle); // open
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    // simulate route change via captured callback
    expect(typeof __onRouteChangeClose).toBe('function');
    await act(async () => {
        __onRouteChangeClose?.();
    });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('navigation', { name: /footer navigation menu/i })).toBeNull();
    });

  it('supports keyboard activation on menu items (Enter/Space) to close the menu', () => {
    renderFooter('landing');

    const toggle = screen.getByRole('button', { name: /open navigation menu/i });
    fireEvent.click(toggle); // open

    const menuAbout = screen
      .getAllByRole('link', { name: 'About' })
      .find(el => el.closest('#footer-mobile-menu'))!;

    // Enter
    fireEvent.keyDown(menuAbout, { key: 'Enter' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    // Re-open and test Space
    fireEvent.click(toggle);
    const menuDonate = screen
      .getAllByRole('link', { name: /Donate/i })
      .find(el => el.closest('#footer-mobile-menu'))!;
    fireEvent.keyDown(menuDonate, { key: ' ' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('renders nothing if config is missing', () => {
    setConfig(null);
    render(
      <MemoryRouter>
        <Footer />
      </MemoryRouter>
    );
    // Nothing rendered (no footer role)
    expect(screen.queryByRole('contentinfo')).toBeNull();
  });
});
