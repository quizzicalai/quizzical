import React from 'react';
import clsx from 'clsx';

// A generic type for icon props, allowing any valid SVG attribute to be passed.
type IconProps = React.SVGProps<SVGSVGElement>;

/**
 * The main application logo component.
 */
export function Logo(props: IconProps) {
  // We separate className from the other props to combine it with default styles.
  const { className, ...rest } = props;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      // Use clsx to merge default classes with any custom className passed in.
      className={clsx("w-8 h-8", className)}
      // Spread the rest of the props onto the SVG element.
      {...rest}
    >
      <path d="M12 2L2 7l10 5 10-5-10-5z" />
      <path d="M2 17l10 5 10-5" />
      <path d="M2 12l10 5 10-5" />
    </svg>
  );
}