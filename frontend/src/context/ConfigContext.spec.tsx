/** ------------------------
 * Inline test bundle for ConfigContext
 * (executes only under Vitest)
 * ------------------------ */
import * as React from 'react';

if ((import.meta as any).vitest) {
  /* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

  // ---------------------------
  // Standardized Imports
  // ---------------------------
  const { vi, describe, it, expect, beforeEach } = (import.meta as any).vitest;
  const { render, screen, act, cleanup, fireEvent } = await import('@testing-library/react');
  const { CONFIG_FIXTURE } = await import('../../tests/fixtures/config.fixture');

  // ---------------------------
  // Globalized Spies & Mocks (avoid TDZ/closure issues)
  // ---------------------------
  const g = globalThis as any;
  g.__InlineErrorSpy ??= vi.fn();
  g.__loadAppConfigMock ??= vi.fn();
  g.__initApiMock ??= vi.fn();
  g.__validateMock ??= vi.fn((x: any) => x);

  // Local aliases (purely for convenience)
  const InlineErrorSpy = g.__InlineErrorSpy as ReturnType<typeof vi.fn>;
  const loadAppConfigMock = g.__loadAppConfigMock as ReturnType<typeof vi.fn>;
  const initApiMock = g.__initApiMock as ReturnType<typeof vi.fn>;
  const validateMock = g.__validateMock as ReturnType<typeof vi.fn>;

  function setupConfigContextMocks() {
    (import.meta as any).env = { ...(import.meta as any).env, DEV: true };
    InlineErrorSpy.mockReset();
    loadAppConfigMock.mockReset();
    initApiMock.mockReset();
    validateMock.mockReset().mockImplementation((x: any) => x);
  }

  // ---------------------------
  // Component Mocks (absolute Vite IDs)
  // ---------------------------
  vi.mock('/src/components/common/Spinner.tsx', () => {
    const Spinner = (props: { message?: string }) =>
      React.createElement('div', { 'data-testid': 'spinner' }, props?.message || 'loading');
    return { Spinner };
  });

  vi.mock('/src/components/common/InlineError.tsx', () => {
    const InlineError = (props: { message: string; onRetry?: () => void }) => {
      (globalThis as any).__InlineErrorSpy({
        message: props?.message,
        onRetry: props?.onRetry,
      });

      const pieces: React.ReactNode[] = [
        React.createElement('div', { key: 'msg', 'data-testid': 'inline-error-message' }, props?.message),
      ];
      if (props?.onRetry) {
        pieces.push(
          React.createElement(
            'button',
            { key: 'retry', 'data-testid': 'retry', onClick: props.onRetry },
            'retry',
          ),
        );
      }
      return React.createElement(
        'div',
        { 'data-testid': 'inline-error', role: 'alert', 'aria-live': 'assertive' },
        ...pieces,
      );
    };
    return { InlineError };
  });

  // ---------------------------
  // Service & Util Mocks
  // ---------------------------
  vi.mock('/src/services/configService.ts', () => ({
    loadAppConfig: (...args: any[]) => (globalThis as any).__loadAppConfigMock(...args),
  }));

  vi.mock('/src/services/apiService.ts', () => ({
    initializeApiService: (...args: any[]) => (globalThis as any).__initApiMock(...args),
  }));

  vi.mock('/src/utils/configValidation.ts', () => ({
    validateAndNormalizeConfig: (raw: unknown) => (globalThis as any).__validateMock(raw),
  }));

  // ---------------------------
  // Driver Helpers
  // ---------------------------
  function mockLoadAppConfigSuccess<T = any>(data: T) {
    (globalThis as any).__loadAppConfigMock.mockResolvedValueOnce(data);
  }

  function mockLoadAppConfigReject(error: any) {
    (globalThis as any).__loadAppConfigMock.mockRejectedValueOnce(error);
  }

  function mockLoadAppConfigCanceled() {
    (globalThis as any).__loadAppConfigMock.mockRejectedValueOnce({
      status: 0,
      code: 'canceled',
      message: 'Request was aborted',
      retriable: false,
      canceled: true,
      name: 'AbortError',
    });
  }

  function mockLoadAppConfigPending() {
    let resolveFn!: (v: any) => void;
    let rejectFn!: (e: any) => void;
    const p = new Promise<any>((resolve, reject) => {
      resolveFn = resolve;
      rejectFn = reject;
    });

    let lastOptions: { signal?: AbortSignal } | undefined;
    (globalThis as any).__loadAppConfigMock.mockImplementationOnce(
      (opts?: { signal?: AbortSignal }) => {
        lastOptions = opts;
        return p;
      },
    );

    return {
      resolveWith(value: any) {
        resolveFn(value);
      },
      rejectWith(error: any) {
        rejectFn(error);
      },
      getLastSignal() {
        return lastOptions?.signal;
      },
    };
  }

  // =======================
  // Tests
  // =======================

  // The module path we’ll import for testing (this very file)
  const MOD_PATH = '/src/context/ConfigContext.tsx';

  describe('ConfigContext (inline spec)', () => {
    beforeEach(() => {
      cleanup();
      vi.resetModules();           // ensure we re-load the instrumented module
      validateMock.mockClear();
      initApiMock.mockClear();
      setupConfigContextMocks();
    });

    it('#16 — renders children IMMEDIATELY against local defaults, then reconciles on successful load; validates + initializes API', async () => {
      const { ConfigProvider, useConfig } = await import(/* @vite-ignore */ MOD_PATH);

      // Arrange: make the HTTP load succeed with the fixture (turnstile:false,
      // distinct from the default turnstile:true so we can prove reconcile).
      mockLoadAppConfigSuccess(CONFIG_FIXTURE);

      function Child() {
        const { config, isLoading, error, features } = useConfig();
        return (
          <div data-testid="child">
            <span data-testid="loading">{String(isLoading)}</span>
            <span data-testid="error">{String(Boolean(error))}</span>
            <span data-testid="appName">{config?.content?.appName ?? ''}</span>
            <span data-testid="turnstile">{String(features.turnstile)}</span>
          </div>
        );
      }

      render(
        <ConfigProvider>
          <Child />
        </ConfigProvider>
      );

      // #16 — children render on the FIRST frame (no full-screen spinner gate),
      // already backed by the normalized local default config.
      expect(screen.queryByTestId('spinner')).toBeNull();
      expect(screen.getByTestId('child')).toBeInTheDocument();
      // Default config is in effect immediately: appName + Turnstile ON.
      expect(screen.getByTestId('appName').textContent).toBe('Quafel');
      expect(screen.getByTestId('turnstile').textContent).toBe('true');
      // API service is initialized from the DEFAULT timeouts up front.
      expect(initApiMock).toHaveBeenCalled();

      // Resolve the background reconcile.
      await act(async () => {});

      // Still no spinner; children remain.
      expect(screen.queryByTestId('spinner')).toBeNull();
      expect(screen.getByTestId('child')).toBeInTheDocument();
      expect(screen.getByTestId('error').textContent).toBe('false');

      // Backend payload validated + reconciled (turnstile flips to fixture's false).
      expect(validateMock).toHaveBeenCalledWith(CONFIG_FIXTURE);
      expect(screen.getByTestId('turnstile').textContent).toBe('false');

      // initializeApiService re-invoked with the reconciled timeouts.
      expect(initApiMock).toHaveBeenCalledWith(CONFIG_FIXTURE.apiTimeouts);
    });

    it('#16 — on non-cancel failure: keeps rendering children against defaults (no full-screen error); reload reconciles', async () => {
      const { ConfigProvider, useConfig } = await import(/* @vite-ignore */ MOD_PATH);

      // 1st call fails -> non-cancel error
      mockLoadAppConfigReject({
        status: 500,
        code: 'http_error',
        message: 'HTTP 500',
        retriable: true,
      });

      function Child() {
        const { features, reload } = useConfig();
        return (
          <div data-testid="children">
            <span data-testid="turnstile">{String(features.turnstile)}</span>
            <button data-testid="do-reload" onClick={reload}>reload</button>
          </div>
        );
      }

      render(
        <ConfigProvider>
          <Child />
        </ConfigProvider>
      );

      // Wait for the background fetch to fail.
      await act(async () => {});

      // #16 — NO full-screen InlineError; children stay rendered against the
      // local defaults, and the safe Turnstile default (ON) is preserved.
      expect(screen.queryByTestId('inline-error')).toBeNull();
      expect(screen.getByTestId('children')).toBeInTheDocument();
      expect(screen.getByTestId('turnstile').textContent).toBe('true');

      // A later reload that succeeds reconciles (turnstile flips to fixture's false).
      mockLoadAppConfigSuccess(CONFIG_FIXTURE);
      fireEvent.click(screen.getByTestId('do-reload'));
      await act(async () => {});

      expect(screen.queryByTestId('inline-error')).toBeNull();
      expect(screen.getByTestId('children')).toBeInTheDocument();
      expect(screen.getByTestId('turnstile').textContent).toBe('false');
    });

    it('ignores cancellation (AbortError/canceled): no error shown; renders children against defaults', async () => {
      const { ConfigProvider } = await import(/* @vite-ignore */ MOD_PATH);

      // Simulate cancellation (the Provider should catch and ignore it)
      mockLoadAppConfigCanceled();

      render(
        <ConfigProvider>
          <div data-testid="children">content</div>
        </ConfigProvider>
      );

      // Allow effect to run and settle
      await act(async () => {});

      // Spinner is gone
      expect(screen.queryByTestId('spinner')).toBeNull();
      // No error UI
      expect(screen.queryByTestId('inline-error')).toBeNull();
      // Consumer children are rendered (against the local default config).
      expect(screen.getByTestId('children')).toBeInTheDocument();

      // #16 — the API service is initialized from the DEFAULT timeouts up front
      // (independent of the canceled background fetch).
      expect(initApiMock).toHaveBeenCalledTimes(1);
    });

    it('aborts in-flight request on unmount (AbortController)', async () => {
      const { ConfigProvider } = await import(/* @vite-ignore */ MOD_PATH);

      // Create a pending promise we can inspect arguments for
      const pending = mockLoadAppConfigPending();

      const { unmount } = render(
        <ConfigProvider>
          <div>child</div>
        </ConfigProvider>
      );

      // Allow the first tick so load is called
      await act(async () => {});

      // Grab the signal passed into loadAppConfig from the pending mock
      const signal: AbortSignal | undefined = pending.getLastSignal();
      expect(signal).toBeDefined();
      expect(signal?.aborted).toBe(false);

      // Unmount triggers abort on the current controller
      unmount();

      // The signal should now be aborted
      expect(signal?.aborted).toBe(true);

      // Clean up: make sure the pending promise won’t dangle
      pending.resolveWith(CONFIG_FIXTURE);
    });

    it('reload(): toggles isLoading and re-runs the background load (children never blocked)', async () => {
      const { ConfigProvider, useConfig } = await import(/* @vite-ignore */ MOD_PATH);

      const first = mockLoadAppConfigPending();

      function Child() {
        const { isLoading, reload } = useConfig();
        return (
          <div data-testid="child">
            <div data-testid="loading">{String(isLoading)}</div>
            <button data-testid="reload" onClick={reload}>reload</button>
          </div>
        );
      }

      render(
        <ConfigProvider>
          <Child />
        </ConfigProvider>
      );

      // Allow initial call to be issued
      await act(async () => {});

      // Children render immediately (no spinner gate); background load pending.
      expect(screen.queryByTestId('spinner')).toBeNull();
      expect(screen.getByTestId('child')).toBeInTheDocument();
      expect(screen.getByTestId('loading').textContent).toBe('true');

      // initializeApiService already invoked once from the DEFAULT timeouts.
      expect(initApiMock).toHaveBeenCalledTimes(1);

      // Now resolve first load -> reconcile.
      first.resolveWith(CONFIG_FIXTURE);
      await act(async () => {});
      expect(screen.getByTestId('loading').textContent).toBe('false');
      // initializeApiService called again on the successful reconcile.
      expect(initApiMock).toHaveBeenCalledTimes(2);

      // Trigger reload -> set up second call as pending.
      const second = mockLoadAppConfigPending();
      fireEvent.click(screen.getByTestId('reload'));
      await act(async () => {});
      // Still no spinner; isLoading flips back to true for the new load.
      expect(screen.queryByTestId('spinner')).toBeNull();
      expect(screen.getByTestId('loading').textContent).toBe('true');

      // Resolve again
      second.resolveWith(CONFIG_FIXTURE);
      await act(async () => {});
      expect(screen.getByTestId('loading').textContent).toBe('false');

      // initializeApiService now called three times: default + 2 reconciles.
      expect(initApiMock).toHaveBeenCalledTimes(3);
    });

    it('#16 — keeps defaults (no full-screen error) when validation throws (invalid backend config)', async () => {
      const { ConfigProvider, useConfig } = await import(/* @vite-ignore */ MOD_PATH);

      // Force validate to throw (simulate invalid config)
      validateMock.mockImplementationOnce(() => {
        throw new Error('bad config');
      });

      // Backend resolves (so the error comes from validation, not HTTP)
      mockLoadAppConfigSuccess(CONFIG_FIXTURE);

      function Child() {
        const { features } = useConfig();
        return (
          <div data-testid="children">
            <span data-testid="turnstile">{String(features.turnstile)}</span>
          </div>
        );
      }

      render(
        <ConfigProvider>
          <Child />
        </ConfigProvider>
      );

      await act(async () => {});

      // No full-screen inline error; children keep rendering against defaults
      // with the safe Turnstile default (ON).
      expect(screen.queryByTestId('inline-error')).toBeNull();
      expect(screen.getByTestId('children')).toBeInTheDocument();
      expect(screen.getByTestId('turnstile').textContent).toBe('true');

      // The DEFAULT timeouts still initialized the API up front; the failed
      // reconcile did not call initializeApiService again.
      expect(initApiMock).toHaveBeenCalledTimes(1);
    });

    it('useConfig throws when used outside of ConfigProvider', async () => {
      const mod = await import(/* @vite-ignore */ MOD_PATH);
      const { useConfig } = mod;

      function LoneConsumer() {
        useConfig();
        return <div>should not render</div>;
      }

      // Suppress expected error noise
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

      expect(() => render(<LoneConsumer />)).toThrow(
        /useConfig must be used within a ConfigProvider/i
      );

      spy.mockRestore();
    });
  });
}
