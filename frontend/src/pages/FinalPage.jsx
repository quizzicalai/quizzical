import { useEffect, memo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import * as apiService from '../services/apiService';
import { useQuizStore } from '../store/quizStore';
import useApi from '../hooks/useApi';
import ResultProfile from '../components/result/ResultProfile';
import FeedbackIcons from '../components/result/FeedbackIcons';
import { InputGroup } from '../components/common/InputGroup';
import Spinner from '../components/common/Spinner';

const FinalPage = memo(() => {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const { reset, startQuiz, setError } = useQuizStore();
  
  // Hook for fetching the result data
  const { data: result, isLoading, error, execute: getResult } = useApi(apiService.getResult);
  
  // A separate hook for the restart action to manage its own loading state
  const { isLoading: isRestarting, execute: createQuiz } = useApi(apiService.startQuiz);

  useEffect(() => {
    // Fire the API call when the component mounts with a valid sessionId.
    if (sessionId) {
      getResult(sessionId).catch(err => {
        if (err.name !== 'AbortError') {
          setError({ message: err.message });
        }
      });
    }
  }, [sessionId, getResult, setError]);

  const handleRestart = async (category) => {
    try {
      reset(); // Reset the old quiz state
      const { quizId } = await createQuiz(category);
      startQuiz({ quizId });
      navigate(`/quiz/${quizId}`);
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError({ message: err.message });
      }
    }
  };

  if (isLoading) {
    return <div className="flex justify-center items-center h-full pt-20"><Spinner size="h-12 w-12" /></div>;
  }

  if (error) {
    return (
      <div className="text-center p-8">
        <p className="text-red-500 mb-4">{error.message}</p>
        <button 
          onClick={() => getResult(sessionId)} 
          className="px-4 py-2 bg-primary text-white rounded-full hover:bg-accent"
        >
          Retry
        </button>
      </div>
    );
  }
  
  if (!result) {
    return <div className="text-center p-8">Result not found.</div>;
  }

  return (
    <section 
      className="w-full max-w-md mx-auto text-center py-8 px-4"
      aria-labelledby="result-profile-title"
    >
      <ResultProfile {...result} id="result-profile-title" />
      <div className="my-8">
        <FeedbackIcons sessionId={sessionId} />
      </div>
      <InputGroup 
        placeholderText="Try another category..."
        onSubmit={handleRestart}
        isLoading={isRestarting}
      />
    </section>
  );
});

export default FinalPage;
