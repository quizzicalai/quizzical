// src/components/common/Image.tsx
import React from 'react';

/**
 * The props for the Image component.
 */
interface ImageProps {
  src: string;
  alt: string;
  className?: string;
}

/**
 * A reusable, robust image component that handles loading errors
 * and provides a smooth visual transition.
 */
const Image: React.FC<ImageProps> = ({ src, alt, className = '' }) => {
  /**
   * Handles the onError event for the image. If the src fails to load,
   * it replaces it with a placeholder image.
   * @param e - The React synthetic event for the image error.
   */
  const handleImageError = (e: React.SyntheticEvent<HTMLImageElement, Event>) => {
    const target = e.target as HTMLImageElement;
    // Prevents an infinite loop if the placeholder also fails to load
    target.onerror = null; 
    // A generic, styled placeholder from an external service
    target.src = `https://placehold.co/600x400/e2e8f0/475569?text=Image+Not+Found`;
  };

  /**
   * Handles the onLoad event for the image, fading it in once loaded.
   * @param e - The React synthetic event for the image load.
   */
  const handleLoad = (e: React.SyntheticEvent<HTMLImageElement, Event>) => {
    (e.target as HTMLImageElement).classList.remove('opacity-0');
  };

  return (
    <img
      src={src}
      alt={alt}
      className={`transition-opacity duration-300 opacity-0 ${className}`}
      onLoad={handleLoad}
      onError={handleImageError}
    />
  );
}

export default Image;