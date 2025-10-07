// src/hooks/useApi.spec.ts
import { describe, it, expect, vi, beforeAll, beforeEach, afterAll, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useApi } from './useApi';
import type { ApiError } from '../types/api';

// Swallow expected AbortError unhandled rejections triggered by intentional test aborts
const swallowExpectedAbort = (err: any) => {
  const msg = String(err?.message ?? '');
  if (err?.name === 'AbortError' || err?.code === 'aborted' || /aborted/i.test(msg)) {
    return; // ignore expected aborts
  }
  // Re-throw unexpected errors so the test runner still fails loudly
  throw err;
};

beforeAll(() => {
  process.on('unhandledRejection', swallowExpectedAbort);
});
afterAll(() => {
  process.off('unhandledRejection', swallowExpectedAbort);
});

// ───────────────────────────────────────────────────────────────────────────────
// Test setup
// ───────────────────────────────────────────────────────────────────────────────

// Force DEV=true so dev-only branches (like console.debug on abort) are stable
beforeEach(() => {
  (import.meta as any).env = { ...(import.meta as any).env, DEV: true };
});

// Silence debug logs (from the hook on aborts)
let debugSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
});
afterEach(() => {
  debugSpy.mockRestore();
});

/** Await one macrotask */
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

/**
 * Deferred service factory.
 * We type the service as (...args: any[]) to avoid tuple optional/rest issues.
 */
function makeDeferredService<T>(opts?: { onCall?: () => void }) {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;

  const inner = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });

  const service = (...args: any[]): Promise<T> => {
    const maybeLast = args.length > 0 ? args[args.length - 1] : undefined;
    const maybeOpts =
      maybeLast && typeof maybeLast === 'object' && 'signal' in (maybeLast as object)
        ? (maybeLast as { signal?: AbortSignal })
        : undefined;

    if (maybeOpts?.signal) {
      if (maybeOpts.signal.aborted) {
        return Promise.reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
      }
      const onAbort = () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
      // Avoid referencing global EventListener type; cast as any.
      (maybeOpts.signal as unknown as { addEventListener: (t: string, l: any, o?: any) => void })
        .addEventListener('abort', onAbort as any, { once: true });

      inner.finally(() => {
        try {
          (maybeOpts.signal as unknown as { removeEventListener: (t: string, l: any, o?: any) => void })
            .removeEventListener('abort', onAbort as any);
        } catch {
          // ignore jsdom quirks
        }
      });
    }

    opts?.onCall?.();
    return inner;
  };

  return {
    service,
    resolve: (v: T) => resolve(v),
    reject: (e: unknown) => reject(e),
  };
}

const makeResolvedService = <T,>(value: T) =>
  (..._args: any[]) => Promise.resolve(value);

const makeRejectedService = (error: unknown) =>
  (..._args: any[]) => Promise.reject(error);

// ───────────────────────────────────────────────────────────────────────────────
// Tests
// ───────────────────────────────────────────────────────────────────────────────

describe('useApi', () => {
  it('execute → success updates data/status/time and calls onSuccess; select maps result', async () => {
    const d = makeDeferredService<{ n: number }>();
    const onSuccess = vi.fn();
    const select = (x: { n: number }) => x.n * 2;

    const { result } = renderHook(() =>
      useApi(d.service, { onSuccess, select, devLabel: 'successTest' })
    );

    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.execute();
    });

    await waitFor(() => expect(result.current.status).toBe('loading'));
    expect(result.current.isLoading).toBe(true);

    await act(async () => {
      d.resolve({ n: 42 });
      await p;
    });

    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.data).toBe(84);
    expect(result.current.lastUpdatedAt).not.toBeNull();
    expect(onSuccess).toHaveBeenCalledWith(84);
  });

  it('immediate + params triggers on mount and revalidate works', async () => {
    const d1 = makeDeferredService<number>();

    const { result, rerender } = renderHook(
      ({ svc }: { svc: (...args: any[]) => Promise<number> }) =>
        useApi<number, []>(svc, { immediate: true, params: [], devLabel: 'immediate' }),
      { initialProps: { svc: d1.service } }
    );

    // immediate -> loading
    await waitFor(() => expect(result.current.status).toBe('loading'));

    await act(async () => {
      d1.resolve(1);
    });

    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe(1);

    // Revalidate (default keepPreviousData=false => loading & data cleared)
    const d2 = makeDeferredService<number>();
    rerender({ svc: d2.service });

    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.revalidate()!;
    });

    await waitFor(() => expect(result.current.status).toBe('loading'));
    expect(result.current.data).toBeNull();

    await act(async () => {
      d2.resolve(2);
      await p;
    });

    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe(2);
  });

  it('keepPreviousData=true → second execute preserves data and flips back after success', async () => {
    const d1 = makeDeferredService<number>();
    const { result, rerender } = renderHook(
      ({ svc }: { svc: (...args: any[]) => Promise<number> }) =>
        useApi<number, []>(svc, { keepPreviousData: true }),
      { initialProps: { svc: d1.service } }
    );

    // First execute → loading → success
    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.execute();
    });
    await waitFor(() => expect(result.current.isLoading).toBe(true));

    await act(async () => {
      d1.resolve(10);
      await p;
    });
    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe(10);

    // Second execute: keep data & set isValidating
    const d2 = makeDeferredService<number>();
    rerender({ svc: d2.service });

    await act(async () => {
      p = result.current.execute();
    });

    await waitFor(() => expect(result.current.isValidating).toBe(true));
    expect(result.current.data).toBe(10); // preserved

    await act(async () => {
      d2.resolve(11);
      await p;
    });

    await waitFor(() => expect(result.current.isValidating).toBe(false));
    expect(result.current.data).toBe(11);
    expect(result.current.status).toBe('success');
  });

  it('new execute aborts previous in-flight request and the new one succeeds', async () => {
    const slow = makeDeferredService<number>();
    const { result, rerender } = renderHook(
      ({ svc }: { svc: (...args: any[]) => Promise<number> }) =>
        useApi<number, []>(svc),
      { initialProps: { svc: slow.service } }
    );

    let first!: Promise<unknown>;
    await act(async () => {
      first = result.current.execute();
    });
    // Preemptively mark as handled to avoid unhandled rejection race
    void first.catch(() => {});

    await waitFor(() => expect(result.current.status).toBe('loading'));

    const fast = makeResolvedService(123);
    rerender({ svc: fast });

    let second!: Promise<unknown>;
    await act(async () => {
      second = result.current.execute();
    });

    // Consume the first abort rejection
    await expect(first).rejects.toMatchObject({ code: 'aborted' });

    await act(async () => {
      await second;
    });

    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe(123);
  });

  it('error path: non-abort sets status=error, normalizes error, calls onError', async () => {
    const err: ApiError = { status: 500, code: 'E', message: 'kaput', retriable: false };
    const svc = makeRejectedService(err);
    const onError = vi.fn();

    const { result } = renderHook(() => useApi(svc, { onError }));

    await act(async () => {
      await expect(result.current.execute()).rejects.toBe(err);
    });

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe(err);
    expect(onError).toHaveBeenCalledWith(err);
  });

  it('dedupeKey: concurrent executes share one service call', async () => {
    const onCall = vi.fn();
    const d = makeDeferredService<number>({ onCall });
    const { result: r1 } = renderHook(() => useApi<number, []>(d.service, { dedupeKey: 'k' }));
    const { result: r2 } = renderHook(() => useApi<number, []>(d.service, { dedupeKey: 'k' }));

    let p1!: Promise<unknown>;
    let p2!: Promise<unknown>;

    await act(async () => {
      p1 = r1.current.execute();
      p2 = r2.current.execute();
    });

    await waitFor(() => {
      expect(r1.current.status).toBe('loading');
      expect(r2.current.status).toBe('loading');
    });

    await act(async () => {
      d.resolve(7);
      await Promise.all([p1, p2]);
    });

    expect(onCall).toHaveBeenCalledTimes(1);
    expect(r1.current.data).toBe(7);
    expect(r2.current.data).toBe(7);
  });

  it('reset aborts active request and returns to idle', async () => {
    const d = makeDeferredService<number>();
    const { result } = renderHook(() => useApi<number, []>(d.service));

    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.execute();
    });
    // Mark as handled before triggering abort to avoid unhandled rejection noise
    void p.catch(() => {});

    await waitFor(() => expect(result.current.status).toBe('loading'));

    await act(async () => {
      result.current.reset(); // aborts active, clears state
    });

    // Consume the abort rejection
    await expect(p).rejects.toMatchObject({ code: 'aborted' });

    expect(result.current.status).toBe('idle');
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isValidating).toBe(false);
    expect(result.current.lastUpdatedAt).toBeNull();
  });

  it('softReset clears error and moves error→idle; leaves success untouched', async () => {
    // First: go to error
    const bad = makeRejectedService({ status: 400, code: 'X', message: 'nope' });
    const { result: r1 } = renderHook(() => useApi(bad));

    await act(async () => {
      await expect(r1.current.execute()).rejects.toBeTruthy();
    });
    expect(r1.current.status).toBe('error');
    expect(r1.current.error).toBeTruthy();

    act(() => {
      r1.current.softReset();
    });
    expect(r1.current.status).toBe('idle');
    expect(r1.current.error).toBeNull();

    // Then: success then soft reset keeps success
    const ok = makeResolvedService(9);
    const { result: r2 } = renderHook(() => useApi(ok));

    await act(async () => {
      await r2.current.execute();
    });
    expect(r2.current.status).toBe('success');

    act(() => r2.current.softReset());
    expect(r2.current.status).toBe('success');
  });

  it('keepPreviousData=false clears prior data on new execute', async () => {
    const d1 = makeResolvedService(1);

    const { result, rerender } = renderHook(
      ({ svc }: { svc: (...args: any[]) => Promise<number> }) => useApi<number, []>(svc),
      { initialProps: { svc: d1 } }
    );

    await act(async () => {
      await result.current.execute();
    });
    expect(result.current.status).toBe('success');
    expect(result.current.data).toBe(1);

    const d2 = makeDeferredService<number>();
    rerender({ svc: d2.service });

    let p!: Promise<unknown>;
    await act(async () => {
      p = result.current.execute();
    });

    await waitFor(() => expect(result.current.status).toBe('loading'));
    expect(result.current.data).toBeNull();

    await act(async () => {
      d2.resolve(2);
      await p;
    });

    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe(2);
  });

  it('changing params re-triggers immediate (dependency on JSON.stringify(params))', async () => {
    const d1 = makeDeferredService<string>();

    type ParamsTuple = [number];

    const { result, rerender } = renderHook(
      ({ params, svc }: { params: ParamsTuple; svc: (...args: any[]) => Promise<string> }) =>
        useApi<string, ParamsTuple>(svc, {
          immediate: true,
          params,
        }),
      { initialProps: { params: [1] as ParamsTuple, svc: d1.service as (...args: any[]) => Promise<string> } }
    );

    await waitFor(() => expect(result.current.status).toBe('loading'));
    await act(async () => {
      d1.resolve('one');
    });
    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe('one');

    const d2 = makeDeferredService<string>();

    await act(async () => {
      rerender({ params: [2] as ParamsTuple, svc: d2.service });
    });

    await waitFor(() => expect(result.current.status).toBe('loading'));
    await act(async () => {
      d2.resolve('two');
    });
    await waitFor(() => expect(result.current.status).toBe('success'));
    expect(result.current.data).toBe('two');
  });

  it('normalizeError: passthrough ApiError, AbortError shape, and unknown Error', async () => {
    // passthrough
    const passthrough: ApiError = { status: 400, code: 'BAD', message: 'no', retriable: false };
    const r1 = renderHook(() => useApi(makeRejectedService(passthrough)));
    await act(async () => {
      await expect(r1.result.current.execute()).rejects.toBe(passthrough);
    });

    // AbortError — thrown directly by service
    const r2 = renderHook(() =>
      useApi(makeRejectedService(Object.assign(new Error('x'), { name: 'AbortError' })))
    );
    await act(async () => {
      await expect(r2.result.current.execute()).rejects.toMatchObject({ code: 'aborted' });
    });

    // Unknown error
    const r3 = renderHook(() => useApi(makeRejectedService(new Error('mystery'))));
    await act(async () => {
      await expect(r3.result.current.execute()).rejects.toMatchObject({
        code: 'unknown',
        message: 'mystery',
      });
    });
  });
});
