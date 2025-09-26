// src/pages/FinalPage.tsx
import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams, Navigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/result/FeedbackIcons';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';
import { Spinner } from '../components/common/Spinner';
import type { ResultProfileData } from '../types/result';
import type { ApiError } from '../types/api';
import { getQuizId } from '../utils/session';

export const FinalPage: React.FC = () => {
  const navigate = useNavigate();
  const { resultId } = useParams<{ resultId: string }>();
  const { config } = useConfig();

  // Pull all reactive bits we need from the store.
  const { quizId: storeQuizId, status: storeStatus, viewData: storeViewData } =
    useQuizStore((s) => ({ quizId: s.quizId, status: s.status, viewData: s.viewData }));

  // Static action reference
  const resetQuiz = useQuizStore.getState().reset;

  const [resultData, setResultData] = useState<ResultProfileData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [shouldRedirect, setShouldRedirect] = useState(false);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels = config?.content?.errors ?? {};

  const effectiveResultId = resultId || storeQuizId || getQuizId();

  useEffect(() => {
    let isCancelled = false;
    const controller = new AbortController();

    const fetchResult = async (id: string) => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await api.getResult(id, { signal: controller.signal });
        if (!isCancelled) {
          setResultData(data);
        }
      } catch (_err: any) {
        if (isCancelled) return;
        // v0: if status/DB can’t produce a result, just send them home.
        setShouldRedirect(true);
      } finally {
        if (!isCancelled) setIsLoading(false);
      }
    };

    // Fast path: if we own this result and already have it in memory, render immediately.
    if (
      storeQuizId &&
      effectiveResultId &&
      storeQuizId === effectiveResultId &&
      storeStatus === 'finished' &&
      storeViewData
    ) {
      setResultData(storeViewData as ResultProfileData);
      setIsLoading(false);
    } else if (effectiveResultId) {
      // Cold-load or shared link: rely on api.getResult (cache-first in v0).
      fetchResult(effectiveResultId);
    } else {
      setError({ status: 404, code: 'not_found', message: errorLabels.resultNotFound || 'No result data found.', retriable: false });
      setIsLoading(false);
    }

    return () => {
      isCancelled = true;
      controller.abort();
    };
  }, [effectiveResultId, errorLabels.resultNotFound, storeQuizId, storeStatus, storeViewData]);

  const handleStartOver = useCallback(() => {
    resetQuiz();
    navigate('/');
  }, [resetQuiz, navigate]);

  const handleCopyShare = useCallback(() => {
    if (effectiveResultId) {
      const shareUrl = `${window.location.origin}/result/${effectiveResultId}`;
      navigator.clipboard.writeText(shareUrl);
    }
  }, [effectiveResultId]);

  if (shouldRedirect) {
    return <Navigate to="/" replace />;
  }

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner message="Loading your result..." />
      </div>
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
    <main className="max-w-3xl mx-auto px-4 py-8">
      <ResultProfile
        result={resultData}
        labels={resultLabels}
        shareUrl={
          effectiveResultId
            ? `${window.location.origin}/result/${effectiveResultId}`
            : undefined
        }
        onCopyShare={handleCopyShare}
        onStartNew={handleStartOver}
      />
      {storeQuizId && storeQuizId === effectiveResultId && (
        <section className="mt-10 pt-8 border-t">
          <FeedbackIcons quizId={storeQuizId} labels={resultLabels.feedback} />
        </section>
      )}
    </main>
  );
};
