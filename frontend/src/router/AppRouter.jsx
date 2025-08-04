// src/router/AppRouter.jsx
import React, { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation, Outlet } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/useQuizStore';
import { Spinner } from '../components/common/Spinner';
import { Header } from '../components/layout/Header';
import { Footer } from '../components/layout/Footer';
import { StaticPage } from '../pages/StaticPage';

// Lazy load pages for better performance
const LandingPage = lazy(() => import('../pages/LandingPage').then(module => ({ default: module.LandingPage })));
const QuizFlowPage = lazy(() => import('../pages/QuizFlowPage').then(module => ({ default: module.QuizFlowPage })));
const FinalPage = lazy(() => import('../pages/FinalPage').then(module => ({ default: module.FinalPage })));

// A wrapper for the main application layout (Header + Content + Footer)
function AppLayout() {
  const { pathname } = useLocation();
  const isLanding = pathname === '/';
  // The footer variant changes based on whether we are on the landing page or not
  const footerVariant = isLanding ? 'landing' : 'quiz';

  return (
    <div className="flex flex-col min-h-screen">
      <Header />
      <div className="flex-grow">
        <Outlet /> {/* Child routes will render here */}
      </div>
      <Footer variant={footerVariant} />
    </div>
  );
}


// Helper to scroll to top and manage focus on navigation
function ScrollAndFocusManager() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
    const mainContent = document.querySelector('main');
    if (mainContent) {
      mainContent.setAttribute('tabindex', '-1');
      mainContent.focus({ preventScroll: true });
    }
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
        
        if (pathname === '/') pageTitle = config?.content?.landingPage?.title ?? baseTitle;
        else if (pathname.startsWith('/quiz')) pageTitle = `Quiz - ${baseTitle}`;
        else if (pathname.startsWith('/result')) pageTitle = `Result - ${baseTitle}`;
        else if (pathname.startsWith('/about')) pageTitle = config?.content?.aboutPage?.title ?? `About - ${baseTitle}`;
        else if (pathname.startsWith('/terms')) pageTitle = config?.content?.termsPage?.title ?? `Terms - ${baseTitle}`;
        else if (pathname.startsWith('/privacy')) pageTitle = config?.content?.privacyPolicyPage?.title ?? `Privacy - ${baseTitle}`;

        document.title = pageTitle;
    }, [pathname, config]);

    return null;
}

// Route guard for the quiz page
function RequireQuiz({ children }) {
  const quizId = useQuizStore((state) => state.quizId);
  if (!quizId) {
      // If no quiz is active, redirect to the landing page.
      return <Navigate to="/" replace />;
  }
  return children;
}

// Simple 404 Component
function NotFound() {
    return (
        <main className="text-center p-10">
            <h1 className="text-2xl font-bold">404 - Page Not Found</h1>
            <p className="text-muted">The page you are looking for does not exist.</p>
            <a href="/" className="text-primary hover:underline mt-4 inline-block">Go Home</a>
        </main>
    );
}

export function AppRouter() {
  return (
    <>
      <ScrollAndFocusManager />
      <DocumentTitleUpdater />
      <Suspense fallback={<div className="h-screen flex items-center justify-center"><Spinner message="Loading..." /></div>}>
        <Routes>
            {/* All primary routes are nested under the main layout */}
            <Route path="/" element={<AppLayout />}>
                <Route index element={<LandingPage />} />
                <Route path="about" element={<StaticPage pageKey="aboutPage" />} />
                <Route path="terms" element={<StaticPage pageKey="termsPage" />} />
                <Route path="privacy" element={<StaticPage pageKey="privacyPolicyPage" />} />
                <Route
                    path="quiz"
                    element={
                    <RequireQuiz>
                        <QuizFlowPage />
                    </RequireQuiz>
                    }
                />
                <Route path="result" element={<FinalPage />} />
                <Route path="result/:resultId" element={<FinalPage />} />
                
                {/* 404 Route */}
                <Route path="*" element={<NotFound />} />
            </Route>
        </Routes>
      </Suspense>
    </>
  );
}