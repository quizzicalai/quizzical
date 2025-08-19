// src/components/common/IconButton.tsx
import React from 'react';

// Define the type for the Icon component prop
type IconComponent = React.ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;

// Define the props interface for the IconButton component
interface IconButtonProps {
  /**
   * The icon component to render inside the button (e.g., ShareIcon).
   */
  Icon: IconComponent;
  /**
   * The function to call when the button is clicked.
   */
  onClick: React.MouseEventHandler<HTMLButtonElement>;
  /**
   * The essential accessible label for screen readers to announce the button's purpose.
   */
  label: string;
  /**
   * Whether the button is disabled.
   * @default false
   */
  disabled?: boolean;
  /**
   * Optional additional Tailwind CSS classes to apply for custom styling.
   */
  className?: string;
}

const IconButton: React.FC<IconButtonProps> = ({
  Icon,
  onClick,
  label,
  disabled = false,
  className = '',
}) => {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      disabled={disabled}
      className={`p-2 rounded-full text-secondary hover:text-primary hover:bg-muted disabled:text-muted disabled:bg-transparent disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 ${className}`}
    >
      {/* CORRECTED: Pass aria-hidden as a boolean value */}
      <Icon className="h-7 w-7" aria-hidden={true} />
    </button>
  );
};

export default IconButton;