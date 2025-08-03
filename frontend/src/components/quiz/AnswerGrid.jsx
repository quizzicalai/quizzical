import { memo } from 'react';
import AnswerTile from './AnswerTile';

/**
 * A memoized component that arranges AnswerTile components in a responsive grid.
 *
 * @param {object} props - The component props.
 * @param {Array<object>} props.answers - An array of answer objects, each with { text, imageUrl }.
 * @param {Function} props.onSelectAnswer - The function to call when any tile is selected, passing the answer text.
 */
const AnswerGrid = memo(({ answers, onSelectAnswer }) => {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {answers.map((answer) => (
        <AnswerTile
          key={answer.text} // Assuming answer text is unique for the key
          text={answer.text}
          imageUrl={answer.imageUrl}
          onClick={() => onSelectAnswer(answer.text)}
        />
      ))}
    </div>
  );
});

export default AnswerGrid;
