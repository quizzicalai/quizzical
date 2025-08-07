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

  // The quizId from the store is now primarily for knowing if the user *just* finished.
  const { quizId: storeQuizId, resetQuiz } = useQuizStore((s) => ({
    quizId: s.quizId,
    resetQuiz: s.reset,
  }));

  const [resultData, setResultData] = useState<ResultProfileData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [shouldRedirect, setShouldRedirect] = useState(false);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels = config?.content?.errors ?? {};

  const effectiveResultId = resultId || storeQuizId || getQuizId();

  useEffect(() => {
    let isCancelled = false;

    const fetchResult = async (id: string) => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await api.getResult(id);
        if (!isCancelled) {
          setResultData(data);
        }
      } catch (err: any) {
        if (!isCancelled) {
          if (err.status === 404 || err.status === 403) {
            // Per FE-207, if a non-owner tries to access, redirect them.
            setShouldRedirect(true);
          } else {
            setError({ ...err, message: errorLabels.resultNotFound || err.message });
          }
        }
      } finally {
        if (!isCancelled) setIsLoading(false);
      }
    };

    if (effectiveResultId) {
      fetchResult(effectiveResultId);
    } else {
      // No ID in URL, store, or session storage. Invalid state.
      setError({ message: errorLabels.resultNotFound || 'No result data found.' });
      setIsLoading(false);
    }

    return () => { isCancelled = true; };
  }, [effectiveResultId, errorLabels.resultNotFound]);

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
    return <div className="flex h-screen items-center justify-center"><Spinner message="Loading your result..." /></div>;
  }

  if (error) {
    return <GlobalErrorDisplay variant="page" error={error} labels={errorLabels} onHome={handleStartOver} />;
  }

  if (!resultData) {
    return <GlobalErrorDisplay variant="page" error={{ message: errorLabels.resultNotFound || 'No result data found.'}} labels={errorLabels} onHome={handleStartOver} />;
  }

  return (
    <main className="max-w-3xl mx-auto px-4 py-8">
      <ResultProfile
        result={resultData}
        labels={resultLabels}
        shareUrl={effectiveResultId ? `${window.location.origin}/result/${effectiveResultId}` : undefined}
        onCopyShare={handleCopyShare}
        onStartNew={handleStartOver}
      />
      {/* Show feedback only if the user just finished this quiz */}
      {storeQuizId && storeQuizId === effectiveResultId && (
        <section className="mt-10 pt-8 border-t">
          <FeedbackIcons quizId={storeQuizId} labels={resultLabels.feedback} />
        </section>
      )}
    </main>
  );
};