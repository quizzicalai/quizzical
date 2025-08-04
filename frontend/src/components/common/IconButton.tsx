import React from 'react';

/**
 * A reusable, accessible button component designed to wrap an icon.
 * It handles interactivity, styling, and accessibility best practices.
 *
 * @param {object} props - The component props.
 * @param {React.ComponentType} props.Icon - The icon component to render inside the button (e.g., ShareIcon).
 * @param {Function} props.onClick - The function to call when the button is clicked.
 * @param {string} props.label - The essential accessible label for screen readers to announce the button's purpose.
 * @param {boolean} [props.disabled=false] - Whether the button is disabled.
 * @param {string} [props.className=''] - Optional additional Tailwind CSS classes to apply for custom styling.
 */
function IconButton({ Icon, onClick, label, disabled = false, className = '' }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      disabled={disabled}
      className={`p-2 rounded-full text-secondary hover:text-primary hover:bg-muted disabled:text-muted disabled:bg-transparent disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 ${className}`}
    >
      <Icon className="h-7 w-7" aria-hidden="true" />
    </button>
  );
}

export default IconButton;
