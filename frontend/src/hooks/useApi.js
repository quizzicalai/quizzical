import { useState, useCallback } from 'react';

/**
 * A custom React hook to manage asynchronous API calls.
 * It handles loading, error, and data states, and provides
 * a function to execute the API call.
 *
 * @param {Function} apiFunc - The function from apiService to be executed.
 * @returns {{
 * data: any,
 * error: Error | null,
 * isLoading: boolean,
 * execute: (...args: any[]) => Promise<any>
 * }}
 */
function useApi(apiFunc) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  /**
   * The `execute` function is a memoized callback that wraps the API call.
   * It manages the full lifecycle of the request: setting loading state,
   * making the call, handling success or error, and cleaning up.
   */
  const execute = useCallback(async (...args) => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    setData(null);

    try {
      const result = await apiFunc(...args, { signal: controller.signal });
      setData(result);
      return result; // Return the result for promise chaining
    } catch (err) {
      // Don't update state if the request was intentionally aborted
      if (err.name !== 'AbortError') {
        setError(err);
      }
      // Re-throw the error so the calling component can also handle it if needed
      throw err;
    } finally {
      // Ensure loading is set to false even if the component unmounts
      // by checking the signal.
      if (!controller.signal.aborted) {
        setIsLoading(false);
      }
    }
  }, [apiFunc]);

  return { data, error, isLoading, execute };
}

export default useApi;

/**
 * --- EXAMPLE USAGE IN A COMPONENT ---
 *
 * import useApi from '../hooks/useApi';
 * import * as apiService from '../services/apiService';
 *
 * function MyComponent() {
 * const { data, isLoading, error, execute: fetchQuiz } = useApi(apiService.getQuizState);
 *
 * useEffect(() => {
 * fetchQuiz('some-quiz-id');
 * }, [fetchQuiz]);
 *
 * if (isLoading) return <Spinner />;
 * if (error) return <div>Error: {error.message}</div>;
 *
 * return <div>{JSON.stringify(data)}</div>;
 * }
 */
