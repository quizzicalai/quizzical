// src/router/AppRouter.tsx
import React, { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation, Outlet } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import { Spinner } from '../components/common/Spinner';
import { Header } from '../components/layout/Header';
import { Footer } from '../components/layout/Footer';
import { AboutPage } from '../pages/AboutPage';
import NotFoundPage from '../pages/NotFoundPage';
import { TermsPage } from '../pages/TermsPage';
import { PrivacyPage } from '../pages/PrivacyPage';

const IS_DEV = import.meta.env.DEV === true;

// Lazy pages
const LandingPage   = lazy(() => import('../pages/LandingPage').then(m => ({ default: m.LandingPage })));
const QuizFlowPage  = lazy(() => import('../pages/QuizFlowPage').then(m => ({ default: m.QuizFlowPage })));
const FinalPage     = lazy(() => import('../pages/FinalPage').then(m => ({ default: m.FinalPage })));

// Dev-only: lazy load so it never ships to prod
const SpritePlayground = IS_DEV
  ? lazy(() => import('../dev/SpritePlayground').then(m => ({ default: m.SpritePlayground })))
  : null as unknown as React.ComponentType;

// ---------- Layout ----------
const AppLayout: React.FC = () => {
  const { pathname } = useLocation();
  const isLanding = pathname === '/';
  const footerVariant = isLanding ? 'landing' : 'quiz';

  return (
    <div className="flex flex-col min-h-screen">
      <Header />
      <div className="flex-grow">
        <Outlet />
      </div>
      <Footer variant={footerVariant} />
    </div>
  );
};

// ---------- UX helpers ----------
const ScrollAndFocusManager: React.FC = () => {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
    const main = document.querySelector('main');
    if (main) {
      main.setAttribute('tabindex', '-1');
      (main as HTMLElement).focus({ preventScroll: true });
    }
  }, [pathname]);
  return null;
};

const DocumentTitleUpdater: React.FC = () => {
  const { config } = useConfig();
  const { pathname } = useLocation();

  useEffect(() => {
    const baseTitle = config?.content?.appName ?? 'Quizzical.ai';
    let pageTitle = baseTitle;

    if (pathname === '/') pageTitle = config?.content?.landingPage?.title ?? baseTitle;
    else if (pathname.startsWith('/quiz')) pageTitle = `Quiz - ${baseTitle}`;
    else if (pathname.startsWith('/result')) pageTitle = `Result - ${baseTitle}`;
    else if (pathname.startsWith('/about')) pageTitle = config?.content?.aboutPage?.title ?? `About - ${baseTitle}`;
    else if (pathname.startsWith('/terms')) pageTitle = config?.content?.termsPage?.title ?? `Terms - ${baseTitle}`;
    else if (pathname.startsWith('/privacy')) pageTitle = config?.content?.privacyPolicyPage?.title ?? `Privacy - ${baseTitle}`;
    else if (IS_DEV && pathname.startsWith('/dev/sprite')) pageTitle = `Sprite Playground - ${baseTitle}`;

    document.title = pageTitle;
  }, [pathname, config]);

  return null;
};

// ---------- Guards ----------
const RequireQuiz: React.FC<React.PropsWithChildren> = ({ children }) => {
  const quizId = useQuizStore((s) => s.quizId);
  if (!quizId) return <Navigate to="/" replace />;
  return <>{children}</>;
};

// ---------- Router ----------
export const AppRouter: React.FC = () => {
  return (
    <>
      <ScrollAndFocusManager />
      <DocumentTitleUpdater />
      <Suspense
        fallback={
          <div className="h-screen flex items-center justify-center">
            <Spinner message="Loading..." />
          </div>
        }
      >
        <Routes>
          <Route path="/" element={<AppLayout />}>
            <Route index element={<LandingPage />} />
            <Route path="about" element={<AboutPage />} />
            <Route path="terms" element={<TermsPage />} />
            <Route path="privacy" element={<PrivacyPage />} />

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

            {/* DEV-ONLY routes */}
            {IS_DEV && SpritePlayground && (
              <Route path="/dev/sprite" element={<SpritePlayground />} />
            )}

            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </Suspense>
    </>
  );
};
