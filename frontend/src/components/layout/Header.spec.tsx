// src/components/layout/Header.spec.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

let __cfg: any = null;

// Mock ConfigContext with a setter so tests can control the config
vi.mock('/src/context/ConfigContext', () => ({
  __setConfig: (c: any) => (__cfg = c),
  useConfig: () => ({ config: __cfg }),
}));

// Mock the Logo icon so we can assert it renders without pulling SVG details
vi.mock('/src/assets/icons/WizardCatIcon', () => ({
  WizardCatIcon: ({ className }: { className?: string }) => (
    <span data-testid="logo" className="inline-flex">
      <svg className={className || ''} />
    </span>
  ),
}));

// Mock react-router-dom's useNavigate
const navigateMock = vi.fn();
vi.mock('react-router-dom', async (orig) => {
  const actual = await (orig() as any);
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

describe('Header', () => {
  const MOD_PATH = '/src/components/layout/Header';

  const setup = async () => {
    const mod = await import(MOD_PATH);
    return mod.Header;
  };

  let __setConfig: any;

  beforeEach(async () => {
    const _cfgModule = (await import('../../context/ConfigContext')) as any;
    __setConfig = _cfgModule.__setConfig;
    vi.resetAllMocks();
    cleanup();
    __cfg = null;
  });
  afterEach(() => {
    cleanup();
  });

  it('renders with appName from config and includes the logo', async () => {
    __setConfig({
      content: { appName: 'Persona Quiz' },
    });

    const Header = await setup();
    render(<Header />);

    // a11y landmark + app name visible
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByText('Persona Quiz')).toBeInTheDocument();

    // aria-label uses appName
    const button = screen.getByRole('button', { name: /go to persona quiz homepage/i });
    expect(button).toBeInTheDocument();

    // logo present
    expect(screen.getByTestId('logo')).toBeInTheDocument();
  });

  it('falls back to "Quizzical.ai" when config or appName is missing', async () => {
    // Case 1: no config at all
    __setConfig(undefined);

    const Header = await setup();
    const { rerender } = render(<Header />);

    expect(screen.getByText('Quizzical.ai')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /go to quizzical\.ai homepage/i })
    ).toBeInTheDocument();

    // Case 2: config without content.appName
    __setConfig({ content: {} });
    rerender(<Header />);
    expect(screen.getByText('Quizzical.ai')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /go to quizzical\.ai homepage/i })
    ).toBeInTheDocument();
  });

  it('navigates to "/" when the logo button is clicked', async () => {
    __setConfig({ content: { appName: 'My App' } });

    const Header = await setup();
    render(<Header />);

    const button = screen.getByRole('button', { name: /go to my app homepage/i });
    fireEvent.click(button);

    expect(navigateMock).toHaveBeenCalledTimes(1);
    expect(navigateMock).toHaveBeenCalledWith('/');
  });

  it('has proper banner role and accessible name on the button', async () => {
    __setConfig({ content: { appName: 'A11y App' } });

    const Header = await setup();
    render(<Header />);

    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /go to a11y app homepage/i })
    ).toBeInTheDocument();
  });
});
