import React from 'react';
import { useConfig } from '../../context/ConfigContext';

type InlineErrorProps = {
  message: string;
  onRetry?: () => void;
};

export function InlineError({ message, onRetry }: InlineErrorProps) {
  const { config } = useConfig();
  const errorLabels = config?.content?.errors ?? {};

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex flex-col items-center justify-center text-center p-4"
    >
      <div className="mx-auto max-w-md p-6 border border-red-300 rounded-lg bg-red-50 text-red-900 shadow-md">
        <h3 className="text-lg font-semibold mb-2">
          {errorLabels.title ?? 'Application Error'}
        </h3>
        <p className="mb-4">{message}</p>
        {onRetry && (
          <button
            className="px-4 py-2 bg-primary text-white rounded hover:opacity-90 transition-opacity"
            onClick={onRetry}
          >
            {errorLabels.retry ?? 'Retry'}
          </button>
        )}
      </div>
    </div>
  );
}
