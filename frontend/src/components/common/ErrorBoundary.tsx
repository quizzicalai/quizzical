// frontend/src/components/common/ErrorBoundary.tsx

import React, { Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';
import { WhimsicalError } from './WhimsicalError';
import { FE_ERROR_CODES } from '../../config/feErrorCodes';

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

  // Whimsical-error-system (2026-06-30): a caught render crash is a FE-only
  // failure, so it uses the FE `QF-FE-...` code map and the SAME WhimsicalError
  // component the rest of the app uses (friendly message + light-grey code).
  const spec = FE_ERROR_CODES.RENDER_CRASH;

  return (
    // role="alert" is provided by the inner WhimsicalError (a <section role=alert>),
    // so this wrapper is a plain layout div to avoid a duplicate alert landmark.
    <div className="flex flex-col items-center justify-center h-screen bg-bg text-fg p-8">
      <div className="text-center max-w-lg">
        <WhimsicalError
          variant="page"
          title="Oops! Something went wrong."
          message={spec.whimsical}
          code={spec.code}
          primaryCta={{ label: 'Start Over', onClick: handleReset }}
        />

        {/* For developers, show error details in non-production environments */}
        {import.meta.env.MODE !== 'production' && error && (
          <details className="mt-6 p-4 bg-card border border-border rounded-md text-left">
            <summary className="cursor-pointer font-semibold">Error Details</summary>
            <pre className="mt-2 text-xs whitespace-pre-wrap">
              {error.stack || error.toString()}
            </pre>
          </details>
        )}
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
    // Forward to the browser console so devs see it locally and so any console-shipping
    // telemetry (Application Insights / Sentry / LogRocket auto-collectors) can pick it up.
    if (import.meta.env.DEV) {
      console.error('Uncaught error:', error, errorInfo);
    } else {
      console.error('Uncaught error:', error?.message);
    }
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