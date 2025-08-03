import React, { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuizStore } from '../store/quizStore';
import useApi from '../hooks/useApi';
import * as apiService from '../services/apiService';
import Spinner from '../components/common/Spinner';
import SynopsisView from '../components/quiz/SynopsisView';
import QuestionView from '../components/quiz/QuestionView';

function QuizFlowPage() {
  const { quizId } = useParams();
  const navigate = useNavigate();

  // Get state and actions from the global store
  const { status, currentView, viewData, hydrateState, setError } = useQuizStore();
  
  // Hooks for API calls
  const { isLoading: isLoadingState, execute: fetchState } = useApi(apiService.getQuizState);
  const { isLoading: isSubmitting, execute: postAnswer } = useApi(apiService.submitAnswer);

  // Fetch the initial state of the quiz when the page loads
  useEffect(() => {
    const loadQuiz = async () => {
      try {
        const stateData = await fetchState(quizId);
        hydrateState({ quizData: stateData });
        if (stateData.status === 'finished') {
          navigate(`/result/${quizId}`, { replace: true });
        }
      } catch (err) {
        setError({ message: err.message });
      }
    };
    loadQuiz();
  }, [quizId, fetchState, hydrateState, setError, navigate]);

  const handleProceed = async () => {
    // This function would be called from the SynopsisView
    // It might involve another API call or just refetching state
    try {
      const stateData = await fetchState(quizId);
      hydrateState({ quizData: stateData });
    } catch (err) {
      setError({ message: err.message });
    }
  };

  const handleSelectAnswer = async (answer) => {
    try {
      await postAnswer(quizId, answer);
      // After submitting, refetch the state to get the next question
      const nextStateData = await fetchState(quizId);
      hydrateState({ quizData: nextStateData });
       if (nextStateData.status === 'finished') {
          navigate(`/result/${quizId}`, { replace: true });
        }
    } catch (err) {
      setError({ message: err.message });
    }
  };

  const renderContent = () => {
    if (status === 'loading' || isLoadingState) {
      return <div className="flex justify-center items-center h-full"><Spinner size="h-12 w-12" /></div>;
    }
    
    if (status === 'error') {
      // The GlobalErrorDisplay will show the message. This is a fallback.
      return <div className="text-center p-8">Could not load quiz.</div>;
    }

    switch (currentView) {
      case 'synopsis':
        return <SynopsisView synopsisData={viewData} onProceed={handleProceed} onResubmitCategory={() => { /* ... */ }} />;
      case 'question':
        return <QuestionView questionData={viewData} onSelectAnswer={handleSelectAnswer} />;
      default:
        return <div className="text-center p-8">Preparing your quiz...</div>;
    }
  };

  return <div className="h-full">{renderContent()}</div>;
}

export default QuizFlowPage;
