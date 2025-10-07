/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';

type TurnstileModule = typeof import('./Turnstile');

interface TurnstileOptions {
  sitekey?: string;
  theme?: string;
  size?: string;
  callback?: (token: string) => void;
  'error-callback'?: () => void;
  'expired-callback'?: () => void;
  [key: string]: any;
}

declare global {
  interface Window {
    resetTurnstile?: () => void;
    __opts?: any;
  }
}

const resetGlobals = () => {
  delete (window as any).turnstile;
  delete (window as any).resetTurnstile;
  delete (window as any).__opts;
};

const importWithEnv = async (
  env: { VITE_TURNSTILE_DEV_MODE: 'true' | 'false'; VITE_TURNSTILE_SITE_KEY?: string },
  preImport?: () => void
): Promise<TurnstileModule['default']> => {
  vi.resetModules();
  vi.stubEnv('VITE_TURNSTILE_DEV_MODE', env.VITE_TURNSTILE_DEV_MODE);
  if (env.VITE_TURNSTILE_SITE_KEY !== undefined) {
    vi.stubEnv('VITE_TURNSTILE_SITE_KEY', env.VITE_TURNSTILE_SITE_KEY);
  } else {
    // ensure it's truly unset
    vi.unstubAllEnvs();
    vi.stubEnv('VITE_TURNSTILE_DEV_MODE', env.VITE_TURNSTILE_DEV_MODE);
  }
  if (preImport) preImport();
  const mod = await import('./Turnstile');
  return mod.default;
};
const createTurnstileMock = () => {
  const render = vi.fn().mockImplementation((_el: HTMLElement, opts: any) => {
    window.__opts = opts; // expose for tests
    return 'widget-id-1';
  });
  const reset = vi.fn();
  const remove = vi.fn();
  const getResponse = vi.fn().mockImplementation((_id: string) => undefined);
  window.turnstile = { render, reset, remove, getResponse };
  return { render, reset, remove, getResponse };
};

beforeEach(() => {
  vi.useFakeTimers();
  cleanup();
  resetGlobals();
});

afterEach(() => {
  try {
    vi.runOnlyPendingTimers();
  } catch (e) {
    // ignore: there may be no pending timers to run in some test runs
    void e;
  }
  vi.useRealTimers();
  cleanup();
  resetGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

/* --------------------------------------------------------------------------------
 * DEV MODE (bypass)
 * ------------------------------------------------------------------------------*/
describe('Turnstile (DEV mode bypass)', () => {
  it('renders bypass notice and auto-calls onVerify after 100ms; resetTurnstile triggers a second token', async () => {
    const Turnstile = await importWithEnv({ VITE_TURNSTILE_DEV_MODE: 'true', VITE_TURNSTILE_SITE_KEY: 'ignored' });

    const onVerify = vi.fn();
    const onError = vi.fn();
    const onExpire = vi.fn();

    render(<Turnstile onVerify={onVerify} onError={onError} onExpire={onExpire} />);

    // Shows dev banner, not loading
    expect(screen.getByText(/Development Mode - Turnstile bypassed/i)).toBeInTheDocument();
    expect(screen.queryByText(/Loading verification/i)).toBeNull();

    // Auto verify after ~100ms
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(onVerify).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    expect(onExpire).not.toHaveBeenCalled();

    // resetTurnstile should produce another dev token
    expect(typeof window.resetTurnstile).toBe('function');
    window.resetTurnstile!();
    expect(onVerify).toHaveBeenCalledTimes(2);
    expect(onVerify.mock.calls[1][0]).toMatch(/^dev-mode-token-reset-/);
  });
});

/* --------------------------------------------------------------------------------
 * NON-DEV MODE (real widget)
 * ------------------------------------------------------------------------------*/
describe('Turnstile (real widget path)', () => {
  it('calls turnstile.render with options, hides loading, installs resetTurnstile, and removes on unmount', async () => {
    const mocks = createTurnstileMock();
    const onVerify = vi.fn();
    const onError = vi.fn();
    const onExpire = vi.fn();

    const Turnstile = await importWithEnv(
      { VITE_TURNSTILE_DEV_MODE: 'false', VITE_TURNSTILE_SITE_KEY: 'site-key-abc' },
      // ensure window.turnstile exists before import (effect runs after mount)
      undefined
    );

    render(<Turnstile onVerify={onVerify} onError={onError} onExpire={onExpire} theme="dark" size="compact" />);

    // Effect should run and render immediately
    expect(mocks.render).toHaveBeenCalledTimes(1);
    expect(window.__opts).toMatchObject({
      sitekey: 'site-key-abc',
      theme: 'dark',
      size: 'compact',
    });
    expect(typeof window.__opts.callback).toBe('function');
    expect(typeof window.__opts['error-callback']).toBe('function');
    expect(typeof window.__opts['expired-callback']).toBe('function');

    // Loading should be gone after successful render
    expect(screen.queryByText(/Loading verification/i)).toBeNull();

    // resetTurnstile delegates to turnstile.reset
    expect(typeof window.resetTurnstile).toBe('function');
    window.resetTurnstile!();
    expect(mocks.reset).toHaveBeenCalledWith('widget-id-1');

    // Unmount triggers remove
    cleanup();
    expect(mocks.remove).toHaveBeenCalledWith('widget-id-1');
  });

  it('propagates callbacks: verify, error, expired (and shows friendly error UI on error)', async () => {
    createTurnstileMock();
    const onVerify = vi.fn();
    const onError = vi.fn();
    const onExpire = vi.fn();

    const Turnstile = await importWithEnv({ VITE_TURNSTILE_DEV_MODE: 'false', VITE_TURNSTILE_SITE_KEY: 'k' });

    render(<Turnstile onVerify={onVerify} onError={onError} onExpire={onExpire} />);

    const opts = window.__opts!;
    act(() => {
      opts.callback('tok-123');
    });
    expect(onVerify).toHaveBeenCalledWith('tok-123');

    act(() => {
      opts['error-callback']();
    });
    expect(onError).toHaveBeenCalled();
    expect(screen.queryByText(/Loading verification/i)).toBeNull();
    expect(screen.getByText(/Verification failed/i)).toBeInTheDocument();

    act(() => {
      opts['expired-callback']();
    });
    expect(onExpire).toHaveBeenCalled();
  });

  it('shows error when site key is missing', async () => {
    // explicitly stub the key as empty so !siteKey is true
    const Turnstile = await importWithEnv({
        VITE_TURNSTILE_DEV_MODE: 'false',
        VITE_TURNSTILE_SITE_KEY: '',   // <-- add this
    });

    createTurnstileMock();
    render(<Turnstile onVerify={() => {}} />);

    expect(screen.queryByText(/Loading verification/i)).toBeNull();
    expect(screen.getByText(/site key not configured/i)).toBeInTheDocument();
    });

  it('retries when turnstile is not on window, then shows "script failed to load" after max retries', async () => {
    // no window.turnstile mock; force retry loop
    const Turnstile = await importWithEnv({ VITE_TURNSTILE_DEV_MODE: 'false', VITE_TURNSTILE_SITE_KEY: 'k' });

    render(<Turnstile onVerify={() => {}} />);

    // Initially shows loading
    expect(screen.getByText(/Loading verification/i)).toBeInTheDocument();

    // 10 retries * 500ms
    await act(async () => {
      vi.advanceTimersByTime(10 * 500 + 5);
    });

    expect(screen.queryByText(/Loading verification/i)).toBeNull();
    expect(screen.getByText(/script failed to load/i)).toBeInTheDocument();
  });

  it('shows "Failed to load verification widget" when render throws', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const renderSpy = vi.fn(() => {
      throw new Error('boom');
    });
    const resetSpy = vi.fn();
    const removeSpy = vi.fn();
    const getResponseSpy = vi.fn().mockReturnValue(undefined);
    window.turnstile = { render: renderSpy, reset: resetSpy, remove: removeSpy, getResponse: getResponseSpy };

    const Turnstile = await importWithEnv({ VITE_TURNSTILE_DEV_MODE: 'false', VITE_TURNSTILE_SITE_KEY: 'k' });

    render(<Turnstile onVerify={() => {}} />);

    expect(screen.queryByText(/Loading verification/i)).toBeNull();
    expect(screen.getByText(/Failed to load verification widget/i)).toBeInTheDocument();

    consoleSpy.mockRestore();
  });

  it('cleans up resetTurnstile on unmount', async () => {
    const { remove } = createTurnstileMock();

    const Turnstile = await importWithEnv({ VITE_TURNSTILE_DEV_MODE: 'false', VITE_TURNSTILE_SITE_KEY: 'k' });

    const { unmount } = render(<Turnstile onVerify={() => {}} />);
    expect(typeof window.resetTurnstile).toBe('function');

    unmount();
    expect(remove).toHaveBeenCalled();
    expect(window.resetTurnstile).toBeUndefined();
  });
});
