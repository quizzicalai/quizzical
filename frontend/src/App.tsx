// src/App.tsx
import React from 'react';
import { BrowserRouter } from 'react-router-dom';

// Foundational providers and components
import { ConfigProvider } from './context/ConfigContext';
import { ThemeInjector } from './styles/ThemeInjector';
import { AppRouter } from './router/AppRouter';
import ErrorBoundary from './components/common/ErrorBoundary';

/**
 * The root application component, orchestrating all providers and routing.
 * It is wrapped in an ErrorBoundary to catch any rendering errors gracefully.
 */
function App() {
  return (
    <ErrorBoundary>
      <ConfigProvider>
        {/* ThemeInjector reads from ConfigProvider, so it must be inside it */}
        <ThemeInjector />
        
        <BrowserRouter>
          {/* The AppRouter now contains all the logic for routes, layout, etc. */}
          <AppRouter />
        </BrowserRouter>
      </ConfigProvider>
    </ErrorBoundary>
  );
}

export default App;
