import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/result/FeedbackIcons';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';
import { Spinner } from '../components/common/Spinner';
import type { ResultProfileData } from '../types/result';
import type { ApiError } from '../types/api';

export const FinalPage: React.FC = () => {
  const navigate = useNavigate();
  const { resultId } = useParams<{ resultId: string }>();
  const { config } = useConfig();

  const { quizId, storeResult, resetQuiz } = useQuizStore((s) => ({
    quizId: s.quizId,
    storeResult: s.viewData,
    resetQuiz: s.reset,
  }));

  const [resultData, setResultData] = useState<ResultProfileData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels = config?.content?.errors ?? {};

  useEffect(() => {
    let isCancelled = false;
    
    const fetchResult = async () => {
      if (!resultId) {
        // If there's no ID in the URL, try to use the result from the store
        if (storeResult && 'archetype' in storeResult) {
          setResultData(storeResult as ResultProfileData);
        }
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      try {
        const data = await api.getResult(resultId);
        if (!isCancelled) setResultData(data);
      } catch (err: any) {
        if (!isCancelled) {
          if (err.status === 404) {
            setError({ ...err, message: errorLabels.sessionExpired || 'This result could not be found or has expired.' });
          } else {
            setError({ ...err, message: errorLabels.resultNotFound || err.message });
          }
        }
      } finally {
        if (!isCancelled) setIsLoading(false);
      }
    };

    fetchResult();

    return () => { isCancelled = true; };
  }, [resultId, storeResult, navigate, errorLabels]);

  const handleStartOver = useCallback(() => {
    resetQuiz();
    navigate('/');
  }, [resetQuiz, navigate]);

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center"><Spinner message="Loading your result..." /></div>;
  }
  
  if (error) {
    return <GlobalErrorDisplay variant="page" error={error} labels={errorLabels} onHome={handleStartOver} />;
  }

  if (!resultData) {
    // This can happen if the user navigates here directly without a quiz in progress.
    // We show an error with a path to start over.
    return <GlobalErrorDisplay variant="page" error={{ message: errorLabels.resultNotFound || 'No result data found.'}} labels={errorLabels} onHome={handleStartOver} />;
  }

  return (
    <main className="max-w-3xl mx-auto px-4 py-8">
      <ResultProfile
        result={resultData}
        labels={resultLabels}
        shareUrl={resultId ? window.location.href : resultData.shareUrl}
        onCopyShare={() => navigator.clipboard.writeText(resultId ? window.location.href : resultData.shareUrl || '')}
        onStartNew={handleStartOver}
      />
      {quizId && (
        <section className="mt-10 pt-8 border-t">
          <FeedbackIcons quizId={quizId} labels={resultLabels.feedback} />
        </section>
      )}
    </main>
  );
}
