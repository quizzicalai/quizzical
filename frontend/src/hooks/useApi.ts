// src/hooks/useApi.ts
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiError } from '../types/api'; // Import our shared ApiError type

const IS_DEV = import.meta.env.DEV === true;

// A tiny in-memory registry for deduping in-flight requests.
const inflight = new Map<string, { promise: Promise<any>; controller: AbortController }>();

// Define the shape of the options object for the hook
type UseApiOptions<TData, TParams extends any[]> = {
  immediate?: boolean;
  params?: TParams;
  select?: (data: TData) => any;
  onSuccess?: (data: any) => void;
  onError?: (err: ApiError) => void;
  keepPreviousData?: boolean;
  dedupeKey?: string;
  devLabel?: string;
};

// Define the shape of the hook's return value
type UseApiReturn<TData, TParams extends any[]> = {
  execute: (...params: TParams) => Promise<any>;
  revalidate: () => Promise<any | void>;
  reset: () => void;
  softReset: () => void;
  data: TData | null;
  error: ApiError | null;
  status: 'idle' | 'loading' | 'success' | 'error';
  isLoading: boolean;
  isValidating: boolean;
  lastUpdatedAt: number | null;
};

// Define the service function's signature
type ServiceFn<TData, TParams extends any[]> = (
  ...args: [...TParams, { signal?: AbortSignal }?]
) => Promise<TData>;

/**
 * A robust, generic hook for making API calls.
 */
export function useApi<TData, TParams extends any[]>(
  serviceFn: ServiceFn<TData, TParams>,
  options: UseApiOptions<TData, TParams> = {}
): UseApiReturn<TData, TParams> {
  const {
    immediate = false,
    params,
    select,
    onSuccess,
    onError,
    keepPreviousData = false,
    dedupeKey,
    devLabel,
  } = options;

  // --- State ---
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [isValidating, setIsValidating] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);

  // --- Refs for safe lifecycle management ---
  const lastParamsRef = useRef<TParams | undefined>(params);
  const lastRequestIdRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null);

  const abortActive = useCallback(() => {
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
  }, []);

  useEffect(() => {
    // This effect runs only once on mount to set up the cleanup function
    return () => {
      abortActive();
    };
  }, [abortActive]);

  const execute = useCallback(async (...callParams: TParams): Promise<any> => {
    const requestId = ++lastRequestIdRef.current;
    lastParamsRef.current = callParams;

    abortActive();

    const isRevalidate = status === 'success' && keepPreviousData;
    if (isRevalidate) {
      setIsValidating(true);
    } else {
      setStatus('loading');
      if (!keepPreviousData) setData(null);
      setError(null);
    }

    let controller: AbortController;
    let promise: Promise<TData>;
    
    if (dedupeKey && inflight.has(dedupeKey)) {
        const reused = inflight.get(dedupeKey)!;
        controller = reused.controller;
        promise = reused.promise;
    } else {
        controller = new AbortController();
        activeControllerRef.current = controller;
        promise = serviceFn(...callParams, { signal: controller.signal });

        if (dedupeKey) {
            inflight.set(dedupeKey, { promise, controller });
            promise.finally(() => {
                if (inflight.get(dedupeKey)?.promise === promise) {
                    inflight.delete(dedupeKey);
                }
            });
        }
    }

    try {
      const rawData = await promise;
      const selectedData = select ? select(rawData) : rawData;

      if (lastRequestIdRef.current === requestId) {
        setData(selectedData);
        setStatus('success');
        setLastUpdatedAt(Date.now());
        onSuccess?.(selectedData);
      }
      return selectedData;
    } catch (err: any) {
      const normalizedError = normalizeError(err);
      if (err.name === 'AbortError') {
        if (IS_DEV) console.debug(`[useApi] Request aborted: "${devLabel}"`);
        throw normalizedError;
      }

      if (lastRequestIdRef.current === requestId) {
        setError(normalizedError);
        setStatus('error');
        onError?.(normalizedError);
      }
      throw normalizedError;
    } finally {
        if (lastRequestIdRef.current === requestId) {
            setIsValidating(false);
            if (activeControllerRef.current === controller) {
                activeControllerRef.current = null;
            }
        }
    }
  }, [abortActive, keepPreviousData, onError, onSuccess, select, serviceFn, status, devLabel, dedupeKey]);

  const revalidate = useCallback(async () => {
    if (!lastParamsRef.current) return;
    return execute(...lastParamsRef.current);
  }, [execute]);

  const reset = useCallback(() => {
    abortActive();
    setStatus('idle');
    setData(null);
    setError(null);
    setIsValidating(false);
    setLastUpdatedAt(null);
  }, [abortActive]);

  const softReset = useCallback(() => {
    setError(null);
    if (status === 'error') {
      setStatus('idle');
    }
  }, [status]);

  useEffect(() => {
    if (immediate && params) {
      execute(...params);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [immediate, JSON.stringify(params)]);

  return {
    execute,
    revalidate,
    reset,
    softReset,
    data,
    error,
    status,
    isLoading: status === 'loading',
    isValidating,
    lastUpdatedAt,
  };
}


function normalizeError(err: any): ApiError {
    if (err && typeof err === 'object' && 'status' in err && 'code' in err && 'message' in err) {
      return err;
    }
    if (err?.name === 'AbortError') {
      return { status: 0, code: 'aborted', message: 'Request was aborted', retriable: false };
    }
    return {
      status: 0,
      code: 'unknown',
      message: err?.message || 'An unknown error occurred',
      retriable: false,
      details: IS_DEV ? err : undefined,
    };
  }