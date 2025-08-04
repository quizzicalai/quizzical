// src/context/ConfigContext.jsx
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Spinner } from '../components/common/Spinner';
import { InlineError } from '../components/common/InlineError';
import { fetchBackendConfig, getMockConfig } from '../services/configService';

const IS_DEV = import.meta.env.DEV === true;
const USE_MOCK = import.meta.env.VITE_USE_MOCK_CONFIG === 'true';

const ConfigContext = createContext(null);

/**
 * Provides application configuration to its children.
 * It handles loading, error, and retry logic for the initial app bootstrap.
 */
export function ConfigProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const controllerRef = useRef(null);

  const loadConfig = useCallback(async () => {
    if (controllerRef.current) {
      controllerRef.current.abort();
    }
    const controller = new AbortController();
    controllerRef.current = controller;

    setIsLoading(true);
    setError(null);

    try {
      const cfg = USE_MOCK
        ? getMockConfig()
        : await fetchBackendConfig({ signal: controller.signal, timeoutMs: 15000 });

      if (!cfg || !cfg.theme || !cfg.content) {
        throw new Error('Received invalid configuration from server.');
      }

      setConfig(cfg);
    } catch (err) {
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
      if (controllerRef.current) {
        controllerRef.current.abort();
      }
    };
  }, [loadConfig]);

  const value = useMemo(() => ({
    config,
    isLoading,
    error,
    reload: loadConfig,
  }), [config, isLoading, error, loadConfig]);

  if (isLoading) {
    return <Spinner message="Loading Configuration..." />;
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
 * @returns {{config: object, isLoading: boolean, error: string | null, reload: () => void}}
 */
export function useConfig() {
  const context = useContext(ConfigContext);
  if (context === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return context;
}