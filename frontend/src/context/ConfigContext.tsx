import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Spinner } from '../components/common/Spinner';
import { InlineError } from '../components/common/InlineError';
import { fetchBackendConfig, getMockConfig } from '../services/configService';
import { AppConfig, validateAndNormalizeConfig } from '../utils/configValidation';

const IS_DEV = import.meta.env.DEV === true;
const USE_MOCK = import.meta.env.VITE_USE_MOCK_CONFIG === 'true';

type ConfigContextValue = {
  config: AppConfig | null;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
};

const ConfigContext = createContext<ConfigContextValue>(null!);

type ConfigProviderProps = {
  children: React.ReactNode;
};

export function ConfigProvider({ children }: ConfigProviderProps) {
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
      const rawConfig = USE_MOCK
        ? getMockConfig()
        : await fetchBackendConfig({ signal: controller.signal, timeoutMs: 15000 });

      // Centralized validation is now the single source of truth
      const validatedConfig = validateAndNormalizeConfig(rawConfig);
      setConfig(validatedConfig);

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

export function useConfig(): ConfigContextValue {
  const context = useContext(ConfigContext);
  if (context === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return context;
}
