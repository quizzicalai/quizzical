import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { DonateCTA } from './DonateCTA';

// Controllable config mock — DonateCTA reads content?.donationUrl from here
// when no explicit `donationUrl` prop is given.
let __cfg: any = null;
vi.mock('../../context/ConfigContext', () => ({
  useConfig: () => ({ config: __cfg }),
}));

const DISMISS_KEY = 'quafel:donate-cta:dismissed';
const URL = 'https://ko-fi.com/quafel';

beforeEach(() => {
  __cfg = null;
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => cleanup());

describe('DonateCTA', () => {
  it('renders nothing when no donationUrl is configured (degrades to hidden)', () => {
    __cfg = { content: { appName: 'Quafel' } }; // no donationUrl
    const { container } = render(<DonateCTA />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('donate-cta')).toBeNull();
  });

  it('renders nothing when donationUrl is an empty/whitespace string', () => {
    __cfg = { content: { appName: 'Quafel', donationUrl: '   ' } };
    const { container } = render(<DonateCTA />);
    expect(container).toBeEmptyDOMElement();
  });

  it('reads donationUrl from app config content and renders the CTA', () => {
    __cfg = { content: { appName: 'Quafel', donationUrl: URL } };
    render(<DonateCTA />);
    expect(screen.getByTestId('donate-cta')).toBeInTheDocument();
    const link = screen.getByTestId('donate-go') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toContain('ko-fi.com/quafel');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toMatch(/noopener/);
  });

  it('preselects the middle ($5) amount chip', () => {
    render(<DonateCTA donationUrl={URL} />);
    expect(screen.getByTestId('donate-amount-5').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('donate-amount-3').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByTestId('donate-amount-10').getAttribute('aria-pressed')).toBe('false');
  });

  it('updates the selected chip and reflects the amount in the donate href', () => {
    render(<DonateCTA donationUrl={URL} />);
    // Default $5
    expect((screen.getByTestId('donate-go') as HTMLAnchorElement).href).toContain('amount=5');

    fireEvent.click(screen.getByTestId('donate-amount-10'));
    expect(screen.getByTestId('donate-amount-10').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('donate-amount-5').getAttribute('aria-pressed')).toBe('false');
    expect((screen.getByTestId('donate-go') as HTMLAnchorElement).href).toContain('amount=10');
  });

  it('dismisses (low-pressure "Maybe later") and persists the dismissal in localStorage', () => {
    const { unmount } = render(<DonateCTA donationUrl={URL} />);
    expect(screen.getByTestId('donate-cta')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('donate-dismiss'));
    expect(screen.queryByTestId('donate-cta')).toBeNull();
    expect(window.localStorage.getItem(DISMISS_KEY)).toBe('1');

    // Repeat takers aren't nagged: a fresh mount stays hidden.
    unmount();
    render(<DonateCTA donationUrl={URL} />);
    expect(screen.queryByTestId('donate-cta')).toBeNull();
  });

  it('explicit donationUrl prop overrides config', () => {
    __cfg = { content: { appName: 'Quafel', donationUrl: '' } };
    render(<DonateCTA donationUrl={URL} />);
    expect(screen.getByTestId('donate-cta')).toBeInTheDocument();
  });
});
