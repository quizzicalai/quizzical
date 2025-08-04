// src/components/common/Spinner.jsx
import React from 'react';

const sizeClasses = {
  sm: 'w-4 h-4 border-2',
  md: 'w-8 h-8 border-4',
  lg: 'w-12 h-12 border-8',
};

export function Spinner({ size = 'md', message }) {
  const sizeClass = sizeClasses[size] || sizeClasses.md;
  return (
    <div className="flex flex-col items-center justify-center gap-4 p-6">
      <div
        className={`animate-spin rounded-full border-primary border-t-transparent ${sizeClass}`}
        aria-label="Loading"
      />
      {message && <span className="text-lg opacity-80">{message}</span>}
    </div>
  );
}