import { useEffect } from 'react';
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

  const { status, currentView, viewData, hydrateState, setError } = useQuizStore();
  
  const { isLoading: isLoadingState, execute: fetchState } = useApi(apiService.getQuizState);
  const { isLoading: isSubmitting, execute: postAnswer } = useApi(apiService.submitAnswer);

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
    if (quizId) {
      loadQuiz();
    }
  }, [quizId, fetchState, hydrateState, setError, navigate]);

  const handleSelectAnswer = async (answer) => {
    try {
      await postAnswer(quizId, answer);
      const nextStateData = await fetchState(quizId);
      hydrateState({ quizData: nextStateData });
       if (nextStateData.status === 'finished') {
          navigate(`/result/${quizId}`, { replace: true });
        }
    } catch (err) {
      setError({ message: err.message });
    }
  };
  
  const handleProceed = () => handleSelectAnswer(null); // Proceeding is like answering with no value

  const renderContent = () => {
    if (status === 'loading' || isLoadingState) {
      return <div className="flex justify-center items-center h-full pt-20"><Spinner size="h-12 w-12" /></div>;
    }

    switch (currentView) {
      case 'synopsis':
        return <SynopsisView synopsisData={viewData} onProceed={handleProceed} onResubmitCategory={() => navigate('/')} />;
      case 'question':
        return <QuestionView questionData={viewData} onSelectAnswer={handleSelectAnswer} />;
      default:
        return <div className="text-center p-8">Preparing your quiz...</div>;
    }
  };

  return <div className="h-full">{renderContent()}</div>;
}

export default QuizFlowPage;
