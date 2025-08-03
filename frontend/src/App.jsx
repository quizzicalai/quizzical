import React, { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

// Foundational providers and components
import { ConfigProvider } from './context/ConfigContext';
import { ThemeInjector } from './styles/ThemeInjector';
import Layout from './components/layout/Layout';
import Spinner from './components/common/Spinner';
import GlobalErrorDisplay from './components/common/GlobalErrorDisplay';

// Lazy Loading Pages for better performance
const LandingPage = lazy(() => import('./pages/LandingPage'));
const QuizFlowPage = lazy(() => import('./pages/QuizFlowPage'));
const FinalPage = lazy(() => import('./pages/FinalPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));

// A full-page loader for Suspense fallback
const PageLoader = () => (
  <div className="flex justify-center items-center h-screen bg-background">
    <Spinner size="h-12 w-12" />
  </div>
);

/**
 * The root application component, orchestrating all providers and routing.
 */
function App() {
  return (
    <ConfigProvider>
      <ThemeInjector />
      
      <BrowserRouter>
        {/* GlobalErrorDisplay is placed here to be visible on all pages */}
        <GlobalErrorDisplay />
        
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<LandingPage />} />
              <Route path="quiz/:quizId" element={<QuizFlowPage />} />
              <Route path="result/:sessionId" element={<FinalPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ConfigProvider>
  );
}

export default App;
