import { memo } from 'react';
import Image from '../common/Image';

/**
 * A memoized component to display the main result profile content.
 *
 * @param {object} props - The component props.
 * @param {string} props.quizTitle - The title of the quiz that was taken.
 * @param {string} props.profileTitle - The title of the user's resulting persona.
 * @param {string} props.imageUrl - The URL for the result image.
 * @param {string} props.description - The descriptive text for the persona, potentially with newlines.
 */
const ResultProfile = memo(({ quizTitle, profileTitle, imageUrl, description }) => {
  return (
    <section aria-label="Quiz Result">
      <p className="text-secondary mb-2">{quizTitle}</p>
      <h1 className="text-4xl md:text-5xl font-extrabold text-primary mb-4">
        {profileTitle}
      </h1>
      <div className="mb-6">
        <Image 
          src={imageUrl} 
          alt={profileTitle} 
          className="w-full h-auto rounded-xl shadow-lg" 
        />
      </div>
      <div className="text-left text-secondary space-y-4">
        {/* Split the description by newline characters to create separate paragraphs */}
        {description.split('\n').map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>
    </section>
  );
});

export default ResultProfile;
