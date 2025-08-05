import React from 'react';

interface ErrorPageProps {
  title?: string;
  message?: string;
  primaryCta?: {
    label: string;
    onClick: () => void;
  };
}

export const ErrorPage: React.FC<ErrorPageProps> = ({
  title = 'Something went wrong',
  message = "We're sorry, but an unexpected error occurred. Please try again later.",
  primaryCta,
}) => {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-8">
      <h1 className="text-4xl font-extrabold text-accent mb-4">{title}</h1>
      <p className="text-secondary mb-8 max-w-md">{message}</p>
      {primaryCta && (
        <button
          onClick={primaryCta.onClick}
          className="px-6 py-3 bg-primary text-white font-bold rounded-full hover:bg-accent focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2"
        >
          {primaryCta.label}
        </button>
      )}
    </div>
  );
};