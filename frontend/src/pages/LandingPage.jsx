import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import useApi from '../hooks/useApi';
import * as apiService from '../services/apiService';
import { InputGroup } from '../components/common/InputGroup';
import GlobalErrorDisplay from '../components/common/GlobalErrorDisplay';

function LandingPage() {
  const navigate = useNavigate();
  const config = useConfig();
  const { startQuiz, setError } = useQuizStore((state) => ({
    startQuiz: state.startQuiz,
    setError: state.setError,
  }));
  const { isLoading, error, execute: createQuiz } = useApi(apiService.startQuiz);

  const handleSubmit = async (category) => {
    try {
      const { quizId } = await createQuiz(category);
      startQuiz({ quizId });
      navigate(`/quiz/${quizId}`);
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError({ message: err.message });
      }
    }
  };

  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-4">
      <h1 className="text-4xl md:text-5xl font-extrabold text-primary mb-8">
        {config.content.landingPage.heading}
      </h1>
      <InputGroup 
        placeholderText={config.content.landingPage.inputPlaceholder}
        onSubmit={handleSubmit}
        isLoading={isLoading}
      />
      {/* The GlobalErrorDisplay component will now handle showing the error. */}
    </div>
  );
}

export default LandingPage;
