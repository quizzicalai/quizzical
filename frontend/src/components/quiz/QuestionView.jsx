import React, { memo } from 'react';
import { AnswerGrid } from './AnswerGrid';

/**
 * A memoized and accessible view component that displays the current question.
 */
const QuestionView = memo(({ questionData, onSelectAnswer }) => {
  if (!questionData) {
    return <div>Loading question...</div>;
  }

  return (
    <section 
      className="w-full max-w-2xl mx-auto text-center py-8 px-4 animate-fade-in"
      role="region"
      aria-label={`Question: ${questionData.questionText}`}
    >
      <h2 className="text-3xl md:text-4xl font-extrabold text-primary mb-8">
        {questionData.questionText}
      </h2>
      <AnswerGrid 
        answers={questionData.answers}
        onSelectAnswer={onSelectAnswer}
      />
    </section>
  );
});

export default QuestionView;
