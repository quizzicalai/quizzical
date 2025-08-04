// src/components/common/InlineError.jsx
import React from 'react';

export function InlineError({ message, onRetry }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center h-screen bg-bg text-fg"
    >
      <div className="mx-auto max-w-md p-6 border border-red-300 rounded-lg bg-red-50 text-red-900 text-center shadow-md">
        <h3 className="text-lg font-semibold mb-2">Application Error</h3>
        <p className="mb-4">{message}</p>
        {onRetry && (
          <button
            className="px-4 py-2 bg-primary text-white rounded hover:opacity-90 transition-opacity"
            onClick={onRetry}
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}