import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/result/FeedbackIcons';
import { SocialShareBar } from '../components/result/SocialShareBar';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';
import { Spinner } from '../components/common/Spinner';
import { HeroCard } from '../components/layout/HeroCard';
import { useQuizMedia } from '../hooks/useQuizMedia';
import type { ResultProfileData } from '../types/result';
import type { ApiError } from '../types/api';
import { getQuizId } from '../utils/session';

export const FinalPage: React.FC = () => {
  const navigate = useNavigate();
  const { resultId: routeId } = useParams<{ resultId: string }>();
  const { config } = useConfig();

  // Pull from store (avoid constructing new objects here)
  const storeQuizId   = useQuizStore((s) => s.quizId);
  const storeStatus   = useQuizStore((s) => s.status);
  const storeViewData = useQuizStore((s) => s.viewData);
  const resetQuiz     = useQuizStore.getState().reset;

  const [resultData, setResultData] = useState<ResultProfileData | null>(null);
  const [isLoading, setIsLoading]   = useState(true);
  const [error, setError]           = useState<ApiError | null>(null);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels  = config?.content?.errors ?? {};

  const effectiveResultId = routeId || storeQuizId || getQuizId();

  // Prevent re-running the effect for the same id (fixes update depth loop)
  const lastLoadedIdRef = useRef<string | null>(null);

  useEffect(() => {
    // If we don’t have an id, show error once.
    if (!effectiveResultId) {
      setError({
        status: 404,
        code: 'not_found',
        message: errorLabels.resultNotFound || 'No result data found.',
        retriable: false,
      });
      setIsLoading(false);
      return;
    }

    // Only run when the id changes.
    if (lastLoadedIdRef.current === effectiveResultId) return;
    lastLoadedIdRef.current = effectiveResultId;

    const controller = new AbortController();

    // Fast path: if this is our current quiz and we already have the finished result in memory.
    if (storeQuizId === effectiveResultId && storeStatus === 'finished' && storeViewData) {
      setResultData(storeViewData as ResultProfileData);
      setIsLoading(false);
      setError(null);
      return;
    }

    // Cold path: fetch from API
    setIsLoading(true);
    setError(null);
    api
      .getResult(effectiveResultId, { signal: controller.signal })
      .then((data) => {
        setResultData(data);
      })
      .catch(() => {
        setError({
          status: 404,
          code: 'not_found',
          message: errorLabels.resultNotFound || 'No result data found.',
          retriable: false,
        });
      })
      .finally(() => {
        setIsLoading(false);
      });

    return () => controller.abort();
  }, [effectiveResultId, storeQuizId, storeStatus, storeViewData, errorLabels.resultNotFound]);

  // Background-poll the media snapshot endpoint while we're on the result
  // page so a slow FAL render still surfaces the winning-character image
  // without a manual refresh. We give it a generous ceiling because some
  // Flux jobs run 60–120s, and only enable polling once we already have
  // a `resultData` lacking an `imageUrl` (otherwise there's nothing to do).
  const needsResultImage = !!resultData && !resultData.imageUrl;
  const { snapshot: mediaSnapshot } = useQuizMedia(effectiveResultId, {
    enabled: needsResultImage,
    expectSynopsisImage: false,
    expectResultImage: true,
    intervalMs: 3_000,
    maxDurationMs: 5 * 60_000,
  });

  // Merge the polled URL into the rendered result without mutating state
  // that other effects depend on.
  const renderedResult: ResultProfileData | null = resultData
    ? {
        ...resultData,
        imageUrl: resultData.imageUrl ?? mediaSnapshot?.resultImageUrl ?? undefined,
      }
    : null;

  const handleStartOver = useCallback(() => {
    resetQuiz();
    navigate('/');
  }, [resetQuiz, navigate]);

  const handleTryNewTopic = useCallback(() => {
    resetQuiz();
    navigate('/', {
      state: { focusTopicInput: true, fromResult: true },
    });
  }, [resetQuiz, navigate]);

  // (handleCopyShare removed — copy + native share are now owned by SocialShareBar.)

  if (isLoading) {
    // Keep loading simple; center it inside a hero card (no hero image)
    return (
      <HeroCard ariaLabel="Result loading" showHero={false}>
        <div className="flex justify-center">
          <Spinner message="Loading your result..." />
        </div>
      </HeroCard>
    );
  }

  if (error) {
    return (
      <GlobalErrorDisplay
        variant="page"
        error={error}
        labels={errorLabels}
        onHome={handleStartOver}
      />
    );
  }

  if (!resultData) {
    return (
      <GlobalErrorDisplay
        variant="page"
        error={{ message: errorLabels.resultNotFound || 'No result data found.' }}
        labels={errorLabels}
        onHome={handleStartOver}
      />
    );
  }

  return (
    <div className="flex items-center justify-center flex-grow">
      <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
        <HeroCard ariaLabel="Result card" showHero={false}>
          <div className="max-w-3xl mx-auto text-center">
            <ResultProfile
              result={renderedResult ?? resultData}
              labels={resultLabels}
              onStartNew={handleStartOver}
            />

            {/* Polished share tray — preview card + social platforms +
                copy-link + native share. Replaces the previous single
                share button that lived inside ResultProfile. */}
            <SocialShareBar
              shareUrl={`${window.location.origin}/result/${effectiveResultId}`}
              shareTitle={
                resultLabels?.share?.socialTitle ||
                (renderedResult?.profileTitle
                  ? `I'm ${renderedResult.profileTitle} — find out yours!`
                  : 'My quiz result')
              }
              shareText={
                resultLabels?.share?.socialDescription ||
                resultLabels?.shareText ||
                'I just took this quiz on Quizzical — check out my result!'
              }
              imageUrl={renderedResult?.imageUrl ?? resultData?.imageUrl ?? undefined}
              previewSubtitle={
                renderedResult?.summary
                  ? renderedResult.summary.split(/\n\s*\n/, 1)[0].slice(0, 140)
                  : undefined
              }
              labels={{
                heading: resultLabels?.shareButton ?? 'Share your result',
                copied: resultLabels?.shareCopied ?? 'Link copied',
                copyLink: resultLabels?.copyLink ?? 'Copy link',
              }}
            />

            <section
              className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center"
              aria-label="Next actions"
            >
              <button
                type="button"
                onClick={handleStartOver}
                className="bg-primary inline-flex min-h-[44px] items-center justify-center rounded-xl px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
              >
                {resultLabels?.startOverButton ?? 'Play Again'}
              </button>
              <button
                type="button"
                onClick={handleTryNewTopic}
                className="inline-flex min-h-[44px] items-center justify-center rounded-xl border border-muted/60 bg-card px-5 py-2.5 text-sm font-semibold text-fg transition-colors hover:bg-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                Try a New Topic
              </button>
            </section>

            {storeQuizId && storeQuizId === effectiveResultId && (
              <section className="mt-10 pt-8 border-t border-muted/50">
                <h2 className="sr-only">Feedback</h2>
                <FeedbackIcons
                  quizId={storeQuizId}
                  labels={{
                    ...(resultLabels as any)?.feedback,
                    prompt: 'What did you think of your result?',
                  }}
                />
              </section>
            )}
          </div>
        </HeroCard>
      </div>
    </div>
  );
};
