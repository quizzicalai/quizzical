import { memo } from 'react';
import Image from '../common/Image';

/**
 * A memoized, interactive tile representing a single answer option.
 *
 * @param {object} props - The component props.
 * @param {string} props.text - The text content of the answer.
 * @param {string} props.imageUrl - The URL for the answer's image.
 * @param {Function} props.onClick - The function to call when the tile is clicked.
 */
const AnswerTile = memo(({ text, imageUrl, onClick }) => {
  return (
    <button
      onClick={onClick}
      className="group aspect-square flex flex-col items-center justify-center p-3 bg-white rounded-xl shadow-md border-2 border-transparent hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent hover:-translate-y-1 transition-all duration-200"
      aria-label={`Select answer: ${text}`}
    >
      <div className="w-full h-2/3 mb-2">
        <Image 
          src={imageUrl} 
          alt={text} 
          className="w-full h-full object-cover rounded-lg" 
        />
      </div>
      <span className="text-center font-medium text-primary leading-tight">
        {text}
      </span>
    </button>
  );
});

export default AnswerTile;
