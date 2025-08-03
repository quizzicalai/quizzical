import { useState, memo } from 'react';
import IconButton from '../common/IconButton';
import { ThumbsUpIcon } from '../../assets/icons/ThumbsUpIcon';
import { ThumbsDownIcon } from '../../assets/icons/ThumbsDownIcon';
import { ShareIcon } from '../../assets/icons/ShareIcon';

/**
 * A memoized component that provides interactive feedback and share buttons.
 *
 * @param {object} props - The component props.
 * @param {string} props.sessionId - The ID of the completed quiz session for submitting feedback.
 */
const FeedbackIcons = memo(({ sessionId }) => {
  const [feedbackSent, setFeedbackSent] = useState(null); // 'up', 'down', or null

  const handleFeedback = (rating) => {
    if (feedbackSent) return; // Prevent multiple submissions
    setFeedbackSent(rating);
    // In a real app, you would call your apiService here:
    // apiService.submitFeedback(sessionId, rating).catch(console.error);
    console.log(`Feedback submitted: ${rating} for session ${sessionId}`);
  };

  const handleShare = () => {
    navigator.clipboard.writeText(window.location.href)
      .then(() => {
        // In a real app, you'd show a toast notification here.
        alert('Result link copied to clipboard!');
      })
      .catch((err) => {
        console.error('Failed to copy link: ', err);
        alert('Could not copy link to clipboard.');
      });
  };

  return (
    <div className="flex items-center justify-center gap-6" aria-label="Actions">
      <button
        onClick={() => handleFeedback('up')}
        disabled={!!feedbackSent}
        aria-label="I liked this result"
        className={`p-2 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-accent ${
          feedbackSent === 'up' ? 'text-accent' : 'text-secondary hover:text-primary'
        }`}
      >
        <ThumbsUpIcon className="h-7 w-7" />
      </button>
      
      <button
        onClick={() => handleFeedback('down')}
        disabled={!!feedbackSent}
        aria-label="I disliked this result"
        className={`p-2 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-accent ${
          feedbackSent === 'down' ? 'text-accent' : 'text-secondary hover:text-primary'
        }`}
      >
        <ThumbsDownIcon className="h-7 w-7" />
      </button>

      <IconButton 
        Icon={ShareIcon} 
        onClick={handleShare} 
        label="Share result" 
      />
    </div>
  );
});

export default FeedbackIcons;
