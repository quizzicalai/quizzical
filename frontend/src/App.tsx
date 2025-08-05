// src/App.tsx
import React from 'react';
import { BrowserRouter } from 'react-router-dom';

// Foundational providers and components
import { ConfigProvider } from './context/ConfigContext';
import { ThemeInjector } from './styles/ThemeInjector';
import { AppRouter } from './router/AppRouter';

/**
 * The root application component, orchestrating all providers and routing.
 */
function App() {
  return (
    <React.StrictMode>
      <ConfigProvider>
        {/* ThemeInjector reads from ConfigProvider, so it must be inside it */}
        <ThemeInjector />
        
        <BrowserRouter>
          {/* The AppRouter now contains all the logic for routes, layout, etc. */}
          <AppRouter />
        </BrowserRouter>
      </ConfigProvider>
    </React.StrictMode>
  );
}

export default App;