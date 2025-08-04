// src/context/ConfigContext.tsx
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Spinner } from '../components/common/Spinner';
import { InlineError } from '../components/common/InlineError';
import { fetchBackendConfig, getMockConfig } from '../services/configService';
import { AppConfig } from '../types/config'; // Import our main config type

const IS_DEV = import.meta.env.DEV === true;
const USE_MOCK = import.meta.env.VITE_USE_MOCK_CONFIG === 'true';

// 1. Define the "contract" for the context's value.
// This tells any component that consumes this context what to expect.
type ConfigContextValue = {
  config: AppConfig | null;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
};

// 2. Create the context with the defined type.
// We initialize it with `null!` because the Provider will always supply a value.
const ConfigContext = createContext<ConfigContextValue>(null!);

type ConfigProviderProps = {
  children: React.ReactNode;
};

/**
 * Provides application configuration to its children.
 * It handles loading, error, and retry logic for the initial app bootstrap.
 */
export function ConfigProvider({ children }: ConfigProviderProps) {
  // 3. Type all state hooks and refs.
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const loadConfig = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setIsLoading(true);
    setError(null);

    try {
      // The fetched config will be implicitly typed as 'any', so we cast it
      // after fetching. A validator function would be even better here.
      const cfg = (USE_MOCK
        ? getMockConfig()
        : await fetchBackendConfig({ signal: controller.signal, timeoutMs: 15000 })) as AppConfig;

      // Simple validation check
      if (!cfg || !cfg.theme || !cfg.content) {
        throw new Error('Received invalid configuration from server.');
      }

      setConfig(cfg);
    } catch (err: any) {
      if (err.name === 'AbortError') {
        if (IS_DEV) console.log('Configuration fetch aborted.');
        return;
      }
      if (IS_DEV) console.error('[ConfigProvider] Failed to load configuration:', err);
      setError('Failed to load application settings. Please check your connection and try again.');
    } finally {
      if (controllerRef.current === controller) {
        setIsLoading(false);
        controllerRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    loadConfig();
    return () => {
      controllerRef.current?.abort();
    };
  }, [loadConfig]);

  // 4. Ensure the `value` object matches the `ConfigContextValue` contract.
  const value: ConfigContextValue = useMemo(() => ({
    config,
    isLoading,
    error,
    reload: loadConfig,
  }), [config, isLoading, error, loadConfig]);

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center"><Spinner message="Loading Configuration..." /></div>;
  }

  if (error) {
    return <InlineError message={error} onRetry={loadConfig} />;
  }

  return (
    <ConfigContext.Provider value={value}>
      {children}
    </ConfigContext.Provider>
  );
}

/**
 * Custom hook to access the application configuration.
 * It's now fully typed to return our `ConfigContextValue`.
 */
export function useConfig(): ConfigContextValue {
  const context = useContext(ConfigContext);
  if (context === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return context;
}