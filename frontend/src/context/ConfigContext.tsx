// frontend/src/context/ConfigContext.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Spinner } from '../components/common/Spinner';
import { InlineError } from '../components/common/InlineError';
import { loadAppConfig } from '../services/configService';
import { initializeApiService } from '../services/apiService';
import { validateAndNormalizeConfig } from '../utils/configValidation';
import type { AppConfig } from '../types/config';

const IS_DEV = import.meta.env.DEV === true;

type ConfigContextValue = {
  config: AppConfig | null;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
};

const ConfigContext = createContext<ConfigContextValue | null>(null);

type ConfigProviderProps = {
  children: React.ReactNode;
};

export function ConfigProvider({ children }: ConfigProviderProps) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    // cancel any in-flight request
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setIsLoading(true);
    setError(null);

    try {
      // fetch *raw* config (unknown/partial), then validate+merge over defaults
      const raw = await loadAppConfig({ signal: controller.signal, timeoutMs: 10_000 });
      const validated = validateAndNormalizeConfig(raw);

      // Initialize API service with timeouts from config
      initializeApiService(validated.apiTimeouts);

      setConfig(validated);
    } catch (err: any) {
      // Ignore benign cancels (StrictMode double invoke, unmount)
      if (err?.canceled === true || err?.name === 'AbortError') {
        if (IS_DEV) console.debug('[ConfigProvider] configuration load aborted (benign)');
        return;
      }
      if (IS_DEV) console.error('[ConfigProvider] failed to load configuration:', err);
      setConfig(null);
      setError('Failed to load application settings. Please check your connection and try again.');
    } finally {
      if (controllerRef.current === controller) {
        setIsLoading(false);
        controllerRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    load();
    return () => {
      controllerRef.current?.abort();
    };
  }, [load]);

  const value: ConfigContextValue = useMemo(
    () => ({
      config,
      isLoading,
      error,
      reload: load,
    }),
    [config, isLoading, error, load]
  );

  return (
    <ConfigContext.Provider value={value}>
      {isLoading ? (
        <div className="flex h-screen items-center justify-center">
          <Spinner message="Loading Configuration..." />
        </div>
      ) : error ? (
        <InlineError message={error} onRetry={load} />
      ) : (
        children
      )}
    </ConfigContext.Provider>
  );
}

export function useConfig(): ConfigContextValue {
  const ctx = useContext(ConfigContext);
  if (ctx === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return ctx;
}