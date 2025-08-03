import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { apiService } from '../services/apiService';
import { Spinner } from '../components/common/Spinner';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';

const ConfigContext = createContext(null);

export const useConfig = () => {
  const context = useContext(ConfigContext);
  if (context === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  // The context value now includes status and retry, but the hook can still just return the config.
  return context.config;
};

export function ConfigProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState('loading'); // 'loading', 'success', 'error'
  const [error, setError] = useState(null);

  const fetchConfiguration = useCallback(async () => {
    setStatus('loading');
    setError(null);
    try {
      // The logic to use mock data can be kept for development if desired
      if (import.meta.env.VITE_USE_MOCK_CONFIG === 'true') {
        console.warn("Using mock configuration for development.");
        // Assuming getMockConfig is still needed for local-only testing
        const { getMockConfig } = await import('../mocks/configMock');
        setConfig(getMockConfig().frontend); // Only store the frontend part
        setStatus('success');
        return;
      }

      // Use the centralized apiService
      const fullConfig = await apiService.getConfig();
      setConfig(fullConfig.frontend); // Only store the frontend part
      setStatus('success');
    } catch (err) {
      console.error("Configuration Fetch Error:", err);
      setError(err.message || 'An unknown error occurred.');
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    fetchConfiguration();
  }, [fetchConfiguration]);

  // Memoize the full context value to prevent unnecessary re-renders
  const value = useMemo(() => ({
    config,
    status,
    retry: fetchConfiguration,
  }), [config, status, fetchConfiguration]);

  if (status === 'loading') {
    // Use the standardized Spinner component
    return <Spinner message="Loading configuration..." />;
  }

  if (status === 'error') {
    // Use the standardized GlobalErrorDisplay component
    return <GlobalErrorDisplay message={error} onRetry={fetchConfiguration} />;
  }

  return (
    <ConfigContext.Provider value={value}>
      {children}
    </ConfigContext.Provider>
  );
}