// src/router/AppRouter.tsx
import React, { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useLocation, Outlet } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import { Spinner } from '../components/common/Spinner';
import { Header } from '../components/layout/Header';
import { Footer } from '../components/layout/Footer';
import { SkipLink } from '../components/common/SkipLink';
import { RouteAnnouncer } from '../components/common/RouteAnnouncer';
import { AboutPage } from '../pages/AboutPage';
import NotFoundPage from '../pages/NotFoundPage';
import { TermsPage } from '../pages/TermsPage';
import { PrivacyPage } from '../pages/PrivacyPage';
import { DonatePage } from '../pages/DonatePage';

const IS_DEV = import.meta.env.DEV === true;

// Lazy pages
const LandingPage   = lazy(() => import('../pages/LandingPage').then(m => ({ default: m.LandingPage })));
const QuizFlowPage  = lazy(() => import('../pages/QuizFlowPage').then(m => ({ default: m.QuizFlowPage })));
const FinalPage     = lazy(() => import('../pages/FinalPage').then(m => ({ default: m.FinalPage })));

// Dev-only: lazy load so it never ships to prod
const ResultPreview = IS_DEV
  ? lazy(() => import('../dev/ResultPreview').then(m => ({ default: m.ResultPreview })))
  : (null as unknown as React.ComponentType);

// PROTOTYPE (prototype/qa-image-enrichment), dev-only — Q&A brand-icon demo.
const QaIconsDemoPage = IS_DEV
  ? lazy(() => import('../proto/QaIconsDemoPage').then(m => ({ default: m.QaIconsDemoPage })))
  : (null as unknown as React.ComponentType);

// ---------- Layout ----------
const AppLayout: React.FC = () => {
  const { pathname } = useLocation();
  const isLanding = pathname === '/';
  const footerVariant = isLanding ? 'landing' : 'quiz';

  return (
    <div className="flex flex-col min-h-screen">
      {/* AC-FE-A11Y-LANDMARK-1: skip-link is the first focusable element. */}
      <SkipLink />
      {/* AC-FE-A11Y-FOCUS-1..3: announce + focus on route changes. */}
      <RouteAnnouncer />
      {/* AC-UX-2026-05-25-PART2 item 1 — Header MUST stay mounted across
          lazy-route transitions. Previously the parent <Suspense> wrapped
          the entire <Routes> tree, so while a route's lazy chunk loaded
          AppLayout (and therefore the Header + Footer) was unmounted and
          replaced by a bare h-screen spinner. Mounting Header + Footer
          OUTSIDE the Suspense boundary keeps the persistent chrome up
          while only <Outlet /> swaps under the fallback. */}
      <Header />
      {/* AC-FE-A11Y-LANDMARK-2/3: exactly one <main> per page, with id="main-content".
         AC-UX-2026-05-25-PART2 item 2 — main is now a flex column so the
         inner page wrappers' `flex-grow` actually fills the available
         space, which combined with Footer's `mt-auto` parks the footer
         at the viewport bottom on short pages. */}
      <main
        id="main-content"
        tabIndex={-1}
        className="flex flex-col flex-grow"
        role="main"
      >
        <Suspense
          fallback={
            <div className="flex flex-grow items-center justify-center">
              <Spinner message="Loading..." />
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </main>
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
    const baseTitle = config?.content?.appName ?? 'Quafel';
    let pageTitle = baseTitle;

    if (pathname === '/') pageTitle = config?.content?.landingPage?.title ?? baseTitle;
    else if (pathname.startsWith('/quiz')) pageTitle = `Quiz - ${baseTitle}`;
    else if (pathname.startsWith('/result')) pageTitle = `Result - ${baseTitle}`;
    else if (pathname.startsWith('/about')) pageTitle = config?.content?.aboutPage?.title ?? `About - ${baseTitle}`;
    else if (pathname.startsWith('/terms')) pageTitle = config?.content?.termsPage?.title ?? `Terms - ${baseTitle}`;
    else if (pathname.startsWith('/privacy')) pageTitle = config?.content?.privacyPolicyPage?.title ?? `Privacy - ${baseTitle}`;
      else if (pathname.startsWith('/donate')) pageTitle = config?.content?.donatePage?.title ?? `Donate - ${baseTitle}`;
    else if (IS_DEV && pathname.startsWith('/dev/result')) pageTitle = `Result Preview - ${baseTitle}`;

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
      <Routes>
        <Route path="/" element={<AppLayout />}>
          <Route index element={<LandingPage />} />
          <Route path="about" element={<AboutPage />} />
          <Route path="terms" element={<TermsPage />} />
          <Route path="privacy" element={<PrivacyPage />} />
          <Route path="donate" element={<DonatePage />} />

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
          {IS_DEV && ResultPreview && (
            <Route path="/dev/result" element={<ResultPreview />} />
          )}
          {IS_DEV && QaIconsDemoPage && (
            <Route path="/dev/qa-icons" element={<QaIconsDemoPage />} />
          )}

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </>
  );
};
