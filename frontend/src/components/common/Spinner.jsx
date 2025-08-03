import React from 'react';

/**
 * A reusable SVG-based spinner component for indicating loading states.
 * @param {object} props - The component props.
 * @param {string} [props.size='h-8 w-8'] - Tailwind CSS classes for height and width.
 * @param {string} [props.color='border-primary'] - Tailwind CSS class for the border color.
 */
function Spinner({ size = 'h-8 w-8', color = 'border-primary' }) {
  return (
    <div
      className={`${size} ${color} border-t-transparent border-solid animate-spin rounded-full border-2`}
      role="status"
      aria-label="Loading"
    >
      <span className="sr-only">Loading...</span>
    </div>
  );
}

export default Spinner;
