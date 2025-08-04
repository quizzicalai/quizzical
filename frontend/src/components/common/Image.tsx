import React from 'react';

/**
 * A reusable, robust image component that handles loading errors
 * and provides a smooth visual transition.
 *
 * @param {object} props - The component props.
 * @param {string} props.src - The source URL for the image.
 * @param {string} props.alt - The essential alternative text for accessibility.
 * @param {string} [props.className=''] - Optional additional Tailwind CSS classes for custom styling.
 */
function Image({ src, alt, className = '' }) {
  const handleImageError = (e) => {
    // Prevents an infinite loop if the placeholder also fails to load
    e.target.onerror = null; 
    // A generic, styled placeholder from an external service
    e.target.src = `https://placehold.co/600x400/e2e8f0/475569?text=Image+Not+Found`;
  };

  return (
    <img
      src={src}
      alt={alt}
      className={`transition-opacity duration-300 opacity-0 ${className}`}
      // When the image successfully loads, fade it in
      onLoad={(e) => e.target.classList.remove('opacity-0')}
      onError={handleImageError}
    />
  );
}

export default Image;