// src/router/AppRouter.jsx
import React, { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/useQuizStore';
import { Spinner } from '../components/common/Spinner';

// Lazy load pages for better performance
const LandingPage = lazy(() => import('../pages/LandingPage').then(module => ({ default: module.LandingPage })));
const QuizFlowPage = lazy(() => import('../pages/QuizFlowPage').then(module => ({ default: module.QuizFlowPage })));
const FinalPage = lazy(() => import('../pages/FinalPage').then(module => ({ default: module.FinalPage })));
const AboutPage = lazy(() => import('../pages/AboutPage').then(module => ({ default: module.AboutPage })));

// Helper to scroll to top on navigation
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

// Helper to update document title based on config
function DocumentTitleUpdater() {
    const { config } = useConfig();
    const { pathname } = useLocation();

    useEffect(() => {
        const baseTitle = config?.content?.appName ?? 'Quizzical';
        let pageTitle = baseTitle;

        if (pathname === '/') {
            pageTitle = config?.content?.landingPage?.title ?? baseTitle;
        } else if (pathname.startsWith('/quiz')) {
            pageTitle = `Quiz - ${baseTitle}`;
        } else if (pathname.startsWith('/result')) {
            pageTitle = `Result - ${baseTitle}`;
        } else if (pathname.startsWith('/about')) {
            pageTitle = `About - ${baseTitle}`;
        }
        document.title = pageTitle;
    }, [pathname, config]);

    return null;
}


// Route guard for the quiz page
function RequireQuiz({ children }) {
  const quizId = useQuizStore((state) => state.quizId);
  return quizId ? children : <Navigate to="/" replace />;
}

// Simple 404 Component
function NotFound() {
    return (
        <div className="text-center p-10">
            <h1 className="text-2xl font-bold">404 - Not Found</h1>
            <p className="text-muted">The page you are looking for does not exist.</p>
            <a href="/" className="text-primary-color hover:underline mt-4 inline-block">Go Home</a>
        </div>
    );
}

export function AppRouter() {
  return (
    <>
      <ScrollToTop />
      <DocumentTitleUpdater />
      <Suspense fallback={<div className="h-screen flex items-center justify-center"><Spinner message="Loading..." /></div>}>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route
            path="/quiz"
            element={
              <RequireQuiz>
                <QuizFlowPage />
              </RequireQuiz>
            }
          />
          <Route path="/result" element={<FinalPage />} />
          <Route path="/result/:resultId" element={<FinalPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </>
  );
}