import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { getMockConfig } from '../mocks/configMock'; // Import mock data

const ConfigContext = createContext(null);

export function ConfigProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [status, setStatus] = useState('loading'); // 'loading', 'success', 'error'

  const fetchConfiguration = useCallback(async () => {
    setStatus('loading');
    try {
      // Best Practice: Use an environment variable to switch to mock data.
      // In Vite, these are prefixed with VITE_. Run `VITE_USE_MOCK_CONFIG=true npm run dev`
      if (import.meta.env.VITE_USE_MOCK_CONFIG === 'true') {
        console.warn("Using mock configuration for development.");
        setConfig(getMockConfig());
        setStatus('success');
        return;
      }

      const response = await fetch('/api/v1/config'); // BFF endpoint
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: Failed to fetch UI configuration.`);
      }
      const data = await response.json();
      setConfig(data);
      setStatus('success');
    } catch (err) {
      console.error("Configuration Fetch Error:", err);
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    fetchConfiguration();
  }, [fetchConfiguration]);

  // Best Practice: Memoize the context value to prevent unnecessary re-renders.
  const value = useMemo(() => ({
    config,
    status,
    retry: fetchConfiguration,
  }), [config, status, fetchConfiguration]);

  if (status === 'loading') {
    return <div style={{ textAlign: 'center', paddingTop: '20%' }}>Loading...</div>;
  }

  if (status === 'error') {
    return (
      <div style={{ textAlign: 'center', paddingTop: '20%', color: 'red' }}>
        <p>Error: Could not load application configuration.</p>
        <button onClick={fetchConfiguration} style={{ marginTop: '1rem', padding: '0.5rem 1rem', cursor: 'pointer' }}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <ConfigContext.Provider value={value}>
      {children}
    </ConfigContext.Provider>
  );
}

export function useConfig() {
  const context = useContext(ConfigContext);
  if (context === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return context.config; // For convenience, the hook can just return the config object.
}
