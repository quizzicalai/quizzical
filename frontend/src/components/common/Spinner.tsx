// src/components/common/Spinner.tsx
import React from 'react';
import clsx from 'clsx';

const sizeClasses = {
  sm: 'w-4 h-4 border-2',
  md: 'w-8 h-8 border-4',
  lg: 'w-12 h-12 border-8',
};

type SpinnerProps = {
  size?: keyof typeof sizeClasses;
  message?: string;
  className?: string;
};

export function Spinner({ size = 'md', message, className }: SpinnerProps) {
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  return (
    <div className={clsx("flex flex-col items-center justify-center gap-4 p-6", className)}>
      <div
        className={clsx(`animate-spin rounded-full border-primary border-t-transparent`, sizeClass)}
        role="status"
        aria-label="Loading"
      />
      {message && <span className="text-lg opacity-80">{message}</span>}
    </div>
  );
}