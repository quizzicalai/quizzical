const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

/**
 * A custom error class for API-related issues.
 */
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * A centralized fetch function to handle all API requests.
 * @param {string} endpoint - The API endpoint to call.
 * @param {object} options - The options for the fetch call (method, body, signal).
 * @returns {Promise<any>} The parsed JSON data.
 */
const apiFetch = async (endpoint, options = {}) => {
  const { body, ...customOptions } = options;
  const headers = { 'Content-Type': 'application/json' };

  const config = {
    ...customOptions,
    headers,
  };

  if (body) {
    config.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, config);

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      message: 'The server returned an unexpected response.',
    }));
    throw new ApiError(errorData.detail || errorData.message || `HTTP error! status: ${response.status}`, response.status);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
};

// --- Exported API Functions ---

/**
 * READ: Gets the entire application configuration.
 */
export const getConfig = ({ signal }) => {
  return apiFetch('/config', { signal });
};

// CREATE: Starts a new quiz session.
export const startQuiz = (category, captchaToken, { signal }) => {
  return apiFetch('/quiz/start', {
    method: 'POST',
    body: { category, captchaToken },
    signal,
  });
};

// UPDATE: Submits an answer.
export const submitAnswer = (quizId, answer, { signal }) => {
  return apiFetch('/quiz/next', {
    method: 'POST',
    body: { quizId, answer },
    signal,
  });
};

// READ (Polling): Checks the status of a background task.
export const getQuizStatus = (quizId, { signal }) => {
  return apiFetch(`/quiz/status/${quizId}`, { signal });
};

// READ: Gets the final, shareable result of a completed quiz.
export const getResult = (sessionId, { signal }) => {
    return apiFetch(`/result/${sessionId}`, { signal });
};

// UPDATE: Submits user feedback for a completed quiz.
export const submitFeedback = (quizId, rating, text, { signal }) => {
    return apiFetch('/feedback', {
        method: 'POST',
        body: { quizId, rating, text },
        signal,
    });
};