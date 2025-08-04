import { useCallback, useEffect, useRef, useState } from 'react';

const IS_DEV = import.meta.env.DEV === true;

// In-memory cache for deduping in-flight requests.
const inflight = new Map();

/**
 * A normalized API error shape.
 * @typedef {object} ApiError
 * @property {number} status - The HTTP status code (0 for network errors).
 * @property {string} code - A machine-readable error code.
 * @property {string} message - A user-friendly error message.
 * @property {boolean} retriable - A hint if the request can be retried.
 * @property {any} [details] - Additional diagnostic info (dev only).
 */

/**
 * @template TData
 * @typedef {'idle'|'loading'|'success'|'error'} ApiStatus
 */

/**
 * A robust, generic hook for making API calls.
 * It handles aborting on unmount, race conditions, deduplication, and provides a rich state.
 *
 * @template TParams - An array of types for the service function parameters.
 * @template TData - The type of data returned by the service function.
 * @param {(...args: [...TParams, {signal?: AbortSignal}?]) => Promise<TData>} serviceFn - The API service function to call.
 * @param {object} [options] - Configuration options for the hook.
 * @param {boolean} [options.immediate=false] - If true, executes the request immediately on mount.
 * @param {TParams} [options.params] - The parameters to use for the immediate call.
 * @param {(data: TData) => any} [options.select] - A function to transform or select a part of the response data.
 * @param {(data: any) => void} [options.onSuccess] - Callback fired on a successful request.
 * @param {(err: ApiError) => void} [options.onError] - Callback fired on a failed request.
 * @param {boolean} [options.keepPreviousData=false] - If true, old data is kept while re-fetching.
 * @param {string} [options.dedupeKey] - A unique key to prevent duplicate in-flight requests.
 * @param {string} [options.devLabel] - A label for logging in development mode.
 * @returns {{
 * execute: (...params: TParams) => Promise<any>,
 * revalidate: () => Promise<any | void>,
 * reset: () => void,
 * softReset: () => void,
 * data: TData | null,
 * error: ApiError | null,
 * status: ApiStatus<TData>,
 * isLoading: boolean,
 * isValidating: boolean,
 * lastUpdatedAt: number | null,
 * }}
 */
export function useApi(serviceFn, options = {}) {
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
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState('idle');
  const [isValidating, setIsValidating] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(null);

  // --- Refs for safe lifecycle management ---
  const mountedRef = useRef(true);
  const lastParamsRef = useRef(params);
  const lastRequestIdRef = useRef(0);
  const activeControllerRef = useRef(null);

  const abortActive = useCallback(() => {
    if (activeControllerRef.current) {
      activeControllerRef.current.abort();
      activeControllerRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortActive();
    };
  }, [abortActive]);

  const safeSet = useCallback((requestId, setter) => {
    if (mountedRef.current && lastRequestIdRef.current === requestId) {
      setter();
    }
  }, []);

  const execute = useCallback(async (...callParams) => {
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

    let controller;
    let promise;
    if (dedupeKey && inflight.has(dedupeKey)) {
      const reused = inflight.get(dedupeKey);
      controller = reused.controller;
      promise = reused.promise;
      if (IS_DEV) console.debug(`[useApi] Reusing in-flight request for key: "${devLabel ?? dedupeKey}"`);
    } else {
      controller = new AbortController();
      activeControllerRef.current = controller;
      promise = serviceFn(...callParams, { signal: controller.signal });

      if (dedupeKey) {
        inflight.set(dedupeKey, { promise, controller, requestId });
        promise.finally(() => {
          const entry = inflight.get(dedupeKey);
          if (entry && entry.requestId === requestId) {
            inflight.delete(dedupeKey);
          }
        });
      }
    }

    try {
      const rawData = await promise;
      const selectedData = select ? select(rawData) : rawData;

      safeSet(requestId, () => {
        setData(selectedData);
        setStatus('success');
        setLastUpdatedAt(Date.now());
      });

      if (onSuccess) onSuccess(selectedData);
      return selectedData;
    } catch (err) {
      const normalizedError = normalizeError(err);
      if (normalizedError.code === 'network_error' && normalizedError.message.includes('abort')) {
        if (IS_DEV) console.debug(`[useApi] Request aborted: "${devLabel}"`);
        throw normalizedError;
      }

      safeSet(requestId, () => {
        setError(normalizedError);
        setStatus('error');
      });

      if (onError) onError(normalizedError);
      throw normalizedError;
    } finally {
      safeSet(requestId, () => {
        setIsValidating(false);
        if (activeControllerRef.current === controller) {
          activeControllerRef.current = null;
        }
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [abortActive, keepPreviousData, onError, onSuccess, select, serviceFn, status, devLabel, dedupeKey]);

  const revalidate = useCallback(async () => {
    if (!lastParamsRef.current) {
      if (IS_DEV) console.warn(`[useApi] revalidate called without any previous params: "${devLabel}"`);
      return;
    }
    return execute(...lastParamsRef.current);
  }, [execute, devLabel]);

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
      if (IS_DEV && devLabel) console.debug(`[useApi] Immediate execution: "${devLabel}"`);
      execute(...params);
    }
    // This effect should only run when `immediate` or `params` change.
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

function normalizeError(err) {
  if (err && typeof err === 'object' && 'status' in err && 'code' in err && 'message' in err) {
    return err;
  }
  // Handle native AbortError
  if (err?.name === 'AbortError') {
    return {
      status: 0,
      code: 'network_error',
      message: 'Request aborted',
      retriable: false,
    };
  }
  if (IS_DEV) console.error('[useApi] Received an unnormalized error:', err);
  return {
    status: 0,
    code: 'unknown_error',
    message: err?.message || 'An unknown error occurred',
    retriable: false,
    details: IS_DEV ? err : undefined,
  };
}