/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';

// Mock ConfigContext so the SUT's `useConfig()` resolves without a provider.
// Leave turnstileSiteKey undefined so the SUT falls back to VITE_TURNSTILE_SITE_KEY
// stubbed per-test via importWithEnv(). `mockConfigState` is mutable so a test
// can flip `isLoading` (used by the #16 site-key-pending affordance).
const mockConfigState: {
  features: { turnstile: boolean; turnstileEnabled: boolean; turnstileSiteKey?: string };
  isLoading: boolean;
} = {
  features: { turnstile: true, turnstileEnabled: true },
  isLoading: false,
};
vi.mock('../../context/ConfigContext', () => ({
  useConfig: () => ({
    features: mockConfigState.features,
    config: null,
    isLoading: mockConfigState.isLoading,
    error: null,
    reload: vi.fn(),
  }),
}));

type TurnstileModule = typeof import('./Turnstile');

declare global {
  interface Window {
    resetTurnstile?: () => void;
    __opts?: any;
    // Do NOT redeclare `turnstile` here — it's already in src/types/turnstile.d.ts
  }
}

const resetGlobals = () => {
  delete (window as any).turnstile;
  delete (window as any).resetTurnstile;
  delete (window as any).__opts;
};

const importWithEnv = async (
  env: { VITE_TURNSTILE_DEV_MODE: 'true' | 'false'; VITE_TURNSTILE_SITE_KEY?: string },
): Promise<TurnstileModule['default']> => {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.stubEnv('VITE_TURNSTILE_DEV_MODE', env.VITE_TURNSTILE_DEV_MODE);
  if (env.VITE_TURNSTILE_SITE_KEY !== undefined) {
    vi.stubEnv('VITE_TURNSTILE_SITE_KEY', env.VITE_TURNSTILE_SITE_KEY);
  }
  const mod = await import('./Turnstile');
  return mod.default;
};

const createTurnstileMock = () => {
  const render = vi.fn().mockImplementation((_el: HTMLElement, opts: any) => {
    window.__opts = opts;
    return 'widget-id-1';
  });
  const reset = vi.fn();
  const remove = vi.fn();
  const getResponse = vi.fn().mockReturnValue(undefined);
  const execute = vi.fn();
  (window as any).turnstile = { render, reset, remove, getResponse, execute }; // <- any
  return { render, reset, remove, getResponse, execute };
};

beforeEach(() => {
  vi.useFakeTimers();
  cleanup();
  resetGlobals();
  // Reset the mutable config-context state to the default (settled, key absent).
  mockConfigState.features = { turnstile: true, turnstileEnabled: true };
  mockConfigState.isLoading = false;
});

afterEach(() => {
  try { vi.runOnlyPendingTimers(); } catch {
    // ignore errors from runOnlyPendingTimers
  }
  vi.useRealTimers();
  cleanup();
  resetGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

/* --------------------------------------------------------------------------------
 * DEV MODE (bypass, no UI, auto token after ~50ms)
 * ------------------------------------------------------------------------------*/
describe('Turnstile (DEV mode bypass)', () => {
  it('auto-calls onVerify after ~50ms and resetTurnstile triggers a second token', async () => {
    const Turnstile = await importWithEnv({ VITE_TURNSTILE_DEV_MODE: 'true', VITE_TURNSTILE_SITE_KEY: 'ignored' });

    const onVerify = vi.fn();
    const onError = vi.fn();
    const onExpire = vi.fn();

    render(<Turnstile onVerify={onVerify} onError={onError} onExpire={onExpire} />);

    // In dev-mode the component renders no UI (silent bypass).
    expect(screen.queryByTestId('turnstile')).toBeNull();
    expect(screen.queryByText(/Loading verification/i)).toBeNull();
    expect(screen.queryByText(/Development Mode/i)).toBeNull();

    await act(async () => { vi.advanceTimersByTime(50); });
    expect(onVerify).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    expect(onExpire).not.toHaveBeenCalled();

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
  it('renders with provided options (compact), installs resetTurnstile, and removes on unmount', async () => {
    const mocks = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'site-key-abc',
    });

    const onVerify = vi.fn();
    const onError = vi.fn();
    const onExpire = vi.fn();

    render(
      <Turnstile
        onVerify={onVerify}
        onError={onError}
        onExpire={onExpire}
        theme="dark"
        size="compact"
        autoExecute={false}
      />
    );

    expect(mocks.render).toHaveBeenCalledTimes(1);
    expect(window.__opts).toMatchObject({
      sitekey: 'site-key-abc',
      theme: 'dark',
      size: 'compact',
    });
    expect(mocks.execute).not.toHaveBeenCalled();

    expect(typeof window.resetTurnstile).toBe('function');
    window.resetTurnstile!();
    expect(mocks.reset).toHaveBeenCalledWith('widget-id-1');
    expect(mocks.execute).not.toHaveBeenCalled();

    cleanup();
    expect(mocks.remove).toHaveBeenCalledWith('widget-id-1');
  });

  it('auto-executes when size="invisible" (default) and autoExecute=true; resetTurnstile does reset+execute', async () => {
    const mocks = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'site-key-xyz',
    });

    render(<Turnstile onVerify={() => {}} />);
    expect(mocks.render).toHaveBeenCalledTimes(1);
    expect(mocks.execute).toHaveBeenCalledWith('widget-id-1');

    window.resetTurnstile!();
    expect(mocks.reset).toHaveBeenCalledWith('widget-id-1');
    expect(mocks.execute).toHaveBeenCalledTimes(2);
  });

  it('propagates verify; expired resets+re-executes when invisible', async () => {
    const mocks = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'k',
    });

    const onVerify = vi.fn();
    const onError = vi.fn();
    const onExpire = vi.fn();

    render(<Turnstile onVerify={onVerify} onError={onError} onExpire={onExpire} />);

    const opts = (window as any).__opts!;
    act(() => { opts.callback('tok-123'); });
    expect(onVerify).toHaveBeenCalledWith('tok-123');

    // QUIZ-UX-POLISH item 3 — expiry must reset() BEFORE execute() so the
    // widget mints a FRESH token rather than returning timeout-or-duplicate.
    mocks.reset.mockClear();
    mocks.execute.mockClear();
    act(() => { opts['expired-callback'](); });
    expect(onExpire).toHaveBeenCalled();
    expect(mocks.reset).toHaveBeenCalledWith('widget-id-1');
    expect(mocks.execute).toHaveBeenCalledWith('widget-id-1');
  });

  // QUIZ-UX-POLISH item 3 — the FIX for the mobile first-attempt failure.
  // Cloudflare's invisible widget commonly fires `error-callback` on the
  // first execute (transient / interactive-challenge race). The widget will
  // NOT recover on its own — it must be reset()+execute()'d again. Previously
  // the component left it stuck with no token (spurious first-attempt
  // failure). It now self-heals silently for a bounded number of errors.
  it('self-heals a transient error by resetting + re-executing without flashing a failure (invisible)', async () => {
    const mocks = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'k',
    });

    const onError = vi.fn();
    render(<Turnstile onVerify={() => {}} onError={onError} />);

    const opts = (window as any).__opts!;
    mocks.reset.mockClear();
    mocks.execute.mockClear();

    // First transient error → silent self-heal (reset + re-execute), no
    // visible failure message.
    act(() => { opts['error-callback'](); });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(mocks.reset).toHaveBeenCalledWith('widget-id-1');
    expect(mocks.execute).toHaveBeenCalledWith('widget-id-1');
    expect(screen.queryByText(/Verification failed/i)).toBeNull();
  });

  it('surfaces the visible failure only after the self-heal budget is exhausted', async () => {
    const mocks = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'k',
    });

    render(<Turnstile onVerify={() => {}} onError={() => {}} />);
    const opts = (window as any).__opts!;

    // Two recoveries are allowed; the 3rd consecutive error surfaces the
    // visible message (a persistent failure can't loop forever).
    act(() => { opts['error-callback'](); }); // recovery 1 (silent)
    act(() => { opts['error-callback'](); }); // recovery 2 (silent)
    expect(screen.queryByText(/Verification failed/i)).toBeNull();

    act(() => { opts['error-callback'](); }); // budget exhausted → visible
    expect(screen.getByText(/Verification failed\. Please try again\./i)).toBeInTheDocument();

    // A good token resets the budget so future transient errors recover again.
    mocks.reset.mockClear();
    act(() => { opts.callback('fresh-token'); });
    act(() => { opts['error-callback'](); });
    expect(mocks.reset).toHaveBeenCalledWith('widget-id-1');
  });

  it('shows error when site key is missing AND config has settled (not loading)', async () => {
    mockConfigState.isLoading = false; // settled — no key is a real error
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: '',
    });

    createTurnstileMock();
    render(<Turnstile onVerify={() => {}} />);
    expect(screen.getByText(/site key not configured/i)).toBeInTheDocument();
    // No dead end: a hard Turnstile failure must offer a way forward.
    expect(screen.getByTestId('turnstile-reload')).toBeInTheDocument();
  });

  // #16 (HITLIST-2026-06-30 review) — in prod the site key arrives ONLY from the
  // background /config reconcile. While that reconcile is still in flight, a
  // missing key is EXPECTED to be transient: show a quiet "preparing" state
  // (the invisible container, no token) instead of flashing the cryptic
  // "site key not configured" error.
  it('does NOT flash the site-key error while config is still loading; renders the invisible container', async () => {
    mockConfigState.isLoading = true; // reconcile in flight — key expected soon
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: '',
    });

    createTurnstileMock();
    render(<Turnstile onVerify={() => {}} />);

    // No cryptic error; the invisible container stays mounted so the widget can
    // render the instant the reconcile lands the key.
    expect(screen.queryByText(/site key not configured/i)).toBeNull();
    expect(screen.getByTestId('turnstile')).toBeInTheDocument();
  });

  it('renders the widget once the reconcile lands the site key (loading -> key present)', async () => {
    // Start in the loading/no-key window.
    mockConfigState.isLoading = true;
    mockConfigState.features = { turnstile: true, turnstileEnabled: true };
    const mocks = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: '',
    });

    const { rerender } = render(<Turnstile onVerify={() => {}} />);
    // No key yet → no widget render, no error.
    expect(mocks.render).not.toHaveBeenCalled();
    expect(screen.queryByText(/site key not configured/i)).toBeNull();

    // Reconcile lands: key present + loading settles.
    mockConfigState.isLoading = false;
    mockConfigState.features = {
      turnstile: true,
      turnstileEnabled: true,
      turnstileSiteKey: 'reconciled-key',
    };
    rerender(<Turnstile onVerify={() => {}} />);

    expect(mocks.render).toHaveBeenCalledTimes(1);
    expect((window as any).__opts).toMatchObject({ sitekey: 'reconciled-key' });
    expect(screen.queryByText(/site key not configured/i)).toBeNull();
  });

  it('retries when window.turnstile is absent, then shows "script failed to load" after max retries', async () => {
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'k',
    });

    render(<Turnstile onVerify={() => {}} />);

    await act(async () => { vi.advanceTimersByTime(10 * 300 + 5); });
    expect(screen.getByText(/script failed to load/i)).toBeInTheDocument();
  });

  it('shows "Failed to load verification widget" when render throws', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const renderSpy = vi.fn(() => { throw new Error('boom'); });
    const resetSpy = vi.fn();
    const removeSpy = vi.fn();
    const getResponseSpy = vi.fn().mockReturnValue(undefined);
    const executeSpy = vi.fn();
    (window as any).turnstile = { render: renderSpy, reset: resetSpy, remove: removeSpy, getResponse: getResponseSpy, execute: executeSpy };

    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'k',
    });

    render(<Turnstile onVerify={() => {}} />);
    expect(screen.getByText(/Failed to load verification widget/i)).toBeInTheDocument();

    consoleSpy.mockRestore();
  });

  it('cleans up resetTurnstile on unmount', async () => {
    const { remove } = createTurnstileMock();
    const Turnstile = await importWithEnv({
      VITE_TURNSTILE_DEV_MODE: 'false',
      VITE_TURNSTILE_SITE_KEY: 'k',
    });

    const { unmount } = render(<Turnstile onVerify={() => {}} />);
    expect(typeof window.resetTurnstile).toBe('function');

    unmount();
    expect(remove).toHaveBeenCalledWith('widget-id-1');
    expect(window.resetTurnstile).toBeUndefined();
  });
});
