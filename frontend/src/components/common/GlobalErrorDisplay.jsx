import React, { useEffect } from 'react';
import { useQuizStore } from '../../store/quizStore';

/**
 * A global "toast" notification component that displays errors from the Zustand store.
 * It automatically dismisses the error after a set duration.
 */
function GlobalErrorDisplay() {
  // Subscribe to the error state and the setError action from the global store.
  const { error, setError } = useQuizStore((state) => ({
    error: state.error,
    setError: state.setError,
  }));

  useEffect(() => {
    let timer;
    // If an error exists, set a timer to clear it after 5 seconds.
    if (error) {
      timer = setTimeout(() => {
        setError({ message: null }); // Clear the error in the store
      }, 5000);
    }
    // Cleanup function to clear the timer if the component unmounts
    // or if the error changes before the timer finishes.
    return () => clearTimeout(timer);
  }, [error, setError]);

  // If there is no error, render nothing.
  if (!error) {
    return null;
  }

  return (
    <div
      role="alert"
      className="fixed bottom-5 right-5 z-50 max-w-sm rounded-lg bg-red-500 px-6 py-4 text-white shadow-lg animate-fade-in-up"
    >
      <div className="flex items-center">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 flex-shrink-0 mr-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p className="font-semibold">{error}</p>
      </div>
    </div>
  );
}

// Add a simple fade-in animation to your tailwind.config.js or global.css
/*
@keyframes fade-in-up {
  from { opacity: 0; transform: translateY(1rem); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in-up {
  animation: fade-in-up 0.3s ease-out forwards;
}
*/

export default GlobalErrorDisplay;
