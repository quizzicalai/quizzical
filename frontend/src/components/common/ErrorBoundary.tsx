// frontend/src/components/common/ErrorBoundary.tsx

import React, { Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';

// ============================================================================
// Types
// ============================================================================

interface Props {
  children: ReactNode;
  fallback?: ReactNode; // Optional custom fallback UI
}

interface State {
  hasError: boolean;
  error?: Error;
}

// ============================================================================
// Default Fallback Component
// ============================================================================

const DefaultFallback = ({ error }: { error?: Error }) => {
  const handleReset = () => {
    // This reloads the page, effectively resetting the application state.
    // In a more complex app, you might clear state and navigate to home.
    window.location.assign(window.location.origin);
  };

  return (
    <div 
      role="alert" 
      className="flex flex-col items-center justify-center h-screen bg-bg text-fg p-8"
    >
      <div className="text-center max-w-lg">
        <h1 className="text-2xl font-bold text-destructive mb-4">
          Oops! Something went wrong.
        </h1>
        <p className="text-muted-foreground mb-6">
          An unexpected error occurred. Please try again. If the problem persists,
          please contact support.
        </p>
        
        {/* For developers, show error details in non-production environments */}
        {import.meta.env.MODE !== 'production' && error && (
          <details className="mb-6 p-4 bg-gray-100 dark:bg-gray-800 rounded-md text-left">
            <summary className="cursor-pointer font-semibold">Error Details</summary>
            <pre className="mt-2 text-xs whitespace-pre-wrap">
              {error.stack || error.toString()}
            </pre>
          </details>
        )}

        <button
          onClick={handleReset}
          className="px-6 py-2 bg-primary text-primary-foreground font-semibold rounded-md hover:bg-primary/90 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50 focus:ring-offset-2 focus:ring-offset-bg"
        >
          Start Over
        </button>
      </div>
    </div>
  );
};


// ============================================================================
// Main Error Boundary Component
// ============================================================================

class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
  };

  /**
   * This lifecycle method is triggered after a descendant component throws an error.
   * It should return a new state object to update the component's state,
   * which will then cause the fallback UI to be rendered on the next pass.
   */
  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  /**
   * This lifecycle method is also triggered after a descendant component throws an error.
   * It's the ideal place for side effects, like logging the error to a monitoring service.
   * This aligns with your requirement for robust logging.
   */
  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // TODO: Integrate with a logging service like Sentry, LogRocket, or Azure App Insights
    console.error("Uncaught error:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      // You can render any custom fallback UI
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return <DefaultFallback error={this.state.error} />;
    }

    // If there's no error, render the children components as normal.
    return this.props.children;
  }
}

export default ErrorBoundary;