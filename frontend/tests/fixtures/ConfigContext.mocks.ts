// frontend/tests/fixtures/configContext.mocks.ts
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { vi } from 'vitest';

export { CONFIG_FIXTURE } from './config.fixture';

// Public spies you assert against in specs
export const InlineErrorSpy = vi.fn();
export const loadAppConfigMock = vi.fn();
export const initApiMock = vi.fn();
export const validateMock = vi.fn((x: any) => x);

// Call from the spec before importing the Provider module
export function setupConfigContextMocks() {
  (import.meta as any).env = { ...(import.meta as any).env, DEV: true };
  InlineErrorSpy.mockReset();
  loadAppConfigMock.mockReset();
  initApiMock.mockReset();
  validateMock.mockReset().mockImplementation((x: any) => x);
}

/* ------------------------- Component mocks ------------------------- */
/**
 * IMPORTANT: Use absolute Vite IDs with a leading `/`.
 * ConfigContext imports with `../components/common/Spinner`,
 * which Vite resolves to `/src/components/common/Spinner.tsx`.
 */
vi.mock('/src/components/common/Spinner.tsx', () => {
  const Spinner = (props: { message?: string }) =>
    React.createElement('div', { 'data-testid': 'spinner' }, props?.message || 'loading');
  return { Spinner };
});

vi.mock('/src/components/common/InlineError.tsx', () => {
  const InlineError = (props: { message: string; onRetry?: () => void }) => {
    InlineErrorSpy({ message: props?.message, onRetry: props?.onRetry });

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

/* ---------------------- Service & util mocks ----------------------- */
// Config service used by ConfigContext: /src/services/configService.ts
vi.mock('/src/services/configService.ts', () => ({
  loadAppConfig: (...args: any[]) => loadAppConfigMock(...args),
}));

// API initializer: /src/services/apiService.ts
vi.mock('/src/services/apiService.ts', () => ({
  initializeApiService: (...args: any[]) => initApiMock(...args),
}));

// Validator: /src/utils/configValidation.ts
vi.mock('/src/utils/configValidation.ts', async () => {
  // If other exports exist we can spread them if needed:
  return {
    validateAndNormalizeConfig: (raw: unknown) => validateMock(raw),
  };
});

/* ----------------------- Driver helpers --------------------------- */
export function mockLoadAppConfigSuccess<T = any>(data: T) {
  loadAppConfigMock.mockResolvedValueOnce(data);
}

export function mockLoadAppConfigReject(error: any) {
  loadAppConfigMock.mockRejectedValueOnce(error);
}

export function mockLoadAppConfigCanceled() {
  loadAppConfigMock.mockRejectedValueOnce({
    status: 0,
    code: 'canceled',
    message: 'Request was aborted',
    retriable: false,
    canceled: true,
  });
}

export function mockLoadAppConfigPending() {
  let resolveFn!: (v: any) => void;
  let rejectFn!: (e: any) => void;
  const p = new Promise<any>((resolve, reject) => {
    resolveFn = resolve;
    rejectFn = reject;
  });

  let lastOptions: { signal?: AbortSignal } | undefined;
  loadAppConfigMock.mockImplementationOnce((opts?: { signal?: AbortSignal }) => {
    lastOptions = opts;
    return p;
  });

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
