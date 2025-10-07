// src/context/ConfigContext.spec.ts
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act, cleanup, fireEvent } from '@testing-library/react';

// Use the shared mocks/driver helpers tailored for ConfigContext tests
import {
  setupConfigContextMocks,
  mockLoadAppConfigSuccess,
  mockLoadAppConfigReject,
  mockLoadAppConfigCanceled,
  mockLoadAppConfigPending,
  validateMock,
  initApiMock,
  CONFIG_FIXTURE,
} from '../../tests/fixtures/ConfigContext.mocks';

// NOTE: We import the module under test AFTER installing the mocks above.
// The `setupConfigContextMocks()` call wires up all module mocks.
setupConfigContextMocks();

const MOD_PATH = 'src/context/ConfigContext';

describe('ConfigContext', () => {
  beforeEach(() => {
    cleanup();
    validateMock.mockClear();
    initApiMock.mockClear();
  });

  it('shows spinner initially then renders children on successful load; validates + initializes API', async () => {
    const { ConfigProvider, useConfig } = await import(/* @vite-ignore */ MOD_PATH);

    // Arrange: make the HTTP load succeed with the fixture
    mockLoadAppConfigSuccess(CONFIG_FIXTURE);

    function Child() {
      const { config, isLoading, error } = useConfig();
      return (
        <div data-testid="child">
          <span data-testid="loading">{String(isLoading)}</span>
          <span data-testid="error">{String(Boolean(error))}</span>
          <span data-testid="appName">{config?.content?.appName ?? ''}</span>
        </div>
      );
    }

    render(
      <ConfigProvider>
        <Child />
      </ConfigProvider>
    );

    // Spinner visible first
    expect(screen.getByTestId('spinner')).toBeInTheDocument();
    expect(screen.getByText(/Loading Configuration/i)).toBeInTheDocument();

    // Resolve async work
    await act(async () => {});

    // Children rendered; spinner gone
    expect(screen.queryByTestId('spinner')).toBeNull();
    expect(screen.getByTestId('child')).toBeInTheDocument();

    // No error on happy path
    expect(screen.getByTestId('error').textContent).toBe('false');

    // Validated config used (validateAndNormalizeConfig called once)
    expect(validateMock).toHaveBeenCalledTimes(1);
    expect(validateMock).toHaveBeenCalledWith(CONFIG_FIXTURE);

    // initializeApiService invoked with timeouts from validated config
    expect(initApiMock).toHaveBeenCalledTimes(1);
    expect(initApiMock).toHaveBeenCalledWith(CONFIG_FIXTURE.apiTimeouts);

    // Child sees the appName from config
    expect(screen.getByTestId('appName').textContent).toBe('Quizzical AI');
  });

  it('on non-cancel failure: shows InlineError with retry; clicking Retry reloads and succeeds', async () => {
    const { ConfigProvider } = await import(/* @vite-ignore */ MOD_PATH);

    // 1st call fails -> non-cancel error
    mockLoadAppConfigReject({
      status: 500,
      code: 'http_error',
      message: 'HTTP 500',
      retriable: true,
    });

    render(
      <ConfigProvider>
        <div data-testid="children">content</div>
      </ConfigProvider>
    );

    // Wait for failure to render error state (spinner disappears)
    await act(async () => {});

    // InlineError visible with Retry button
    const inline = screen.getByTestId('inline-error');
    expect(inline).toBeInTheDocument();
    expect(screen.getByTestId('retry')).toBeInTheDocument();

    // 2nd call will succeed
    mockLoadAppConfigSuccess(CONFIG_FIXTURE);

    // Click retry
    fireEvent.click(screen.getByTestId('retry'));

    // Allow reload to finish
    await act(async () => {});

    // Error gone; children displayed
    expect(screen.queryByTestId('inline-error')).toBeNull();
    expect(screen.getByTestId('children')).toBeInTheDocument();

    // initializeApiService called exactly once (on the successful load)
    expect(initApiMock).toHaveBeenCalledTimes(1);
  });

  it('ignores cancellation (AbortError/canceled): no error shown; ends loading and renders children slot', async () => {
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
    // Consumer children are rendered (config is null but error is also null)
    expect(screen.getByTestId('children')).toBeInTheDocument();

    // Cancel should not initialize API
    expect(initApiMock).not.toHaveBeenCalled();
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

  it('reload(): calls through to trigger a new load', async () => {
    const { ConfigProvider, useConfig } = await import(/* @vite-ignore */ MOD_PATH);

    const first = mockLoadAppConfigPending();

    function Child() {
      const { isLoading, reload } = useConfig();
      return (
        <div>
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

    // While pending, isLoading should still be true (spinner rendered by provider wrapper)
    expect(screen.getByTestId('spinner')).toBeInTheDocument();

    // Now resolve first load
    first.resolveWith(CONFIG_FIXTURE);
    await act(async () => {});

    expect(screen.queryByTestId('spinner')).toBeNull();

    // Trigger reload -> set up second call as success too
    const second = mockLoadAppConfigPending();
    fireEvent.click(screen.getByTestId('reload'));

    await act(async () => {});
    // Spinner returns for the second load
    expect(screen.getByTestId('spinner')).toBeInTheDocument();

    // Resolve again
    second.resolveWith(CONFIG_FIXTURE);
    await act(async () => {});
    expect(screen.queryByTestId('spinner')).toBeNull();

    // initializeApiService should have been called twice (once per successful load)
    expect(initApiMock).toHaveBeenCalledTimes(2);
  });

  it('renders InlineError with friendly message when validation throws (invalid config)', async () => {
    const { ConfigProvider } = await import(/* @vite-ignore */ MOD_PATH);

    // Force validate to throw (simulate invalid config)
    validateMock.mockImplementationOnce(() => {
      throw new Error('bad config');
    });

    // Backend resolves (so the error comes from validation, not HTTP)
    mockLoadAppConfigSuccess(CONFIG_FIXTURE);

    render(
      <ConfigProvider>
        <div>children</div>
      </ConfigProvider>
    );

    await act(async () => {});

    // Inline error shown with retry action
    const err = screen.getByTestId('inline-error');
    expect(err).toBeInTheDocument();
    expect(screen.getByTestId('retry')).toBeInTheDocument();

    // API should not have been initialized on validation failure
    expect(initApiMock).not.toHaveBeenCalled();
  });

  it('useConfig throws when used outside of ConfigProvider', async () => {
    const mod = await import(/* @vite-ignore */ MOD_PATH);
    const { useConfig } = mod;

    function LoneConsumer() {
      // This call should throw at render time
      // eslint-disable-next-line react-hooks/rules-of-hooks
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
