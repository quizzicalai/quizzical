import React, { useState, memo } from 'react';
import { Image } from '../common/Image';
import { InputGroup } from '../common/InputGroup';
import { IconButton } from '../common/IconButton';
import { ArrowIcon } from '../../assets/icons/ArrowIcon';
import { EditIcon } from '../../assets/icons/EditIcon'; // Assuming you create this icon

/**
 * A memoized view component to display the quiz synopsis.
 * It handles its own editing state for better encapsulation.
 */
const SynopsisView = memo(({ synopsisData, onProceed, onResubmitCategory }) => {
  const [isEditing, setIsEditing] = useState(false);

  const handleResubmit = (newCategory) => {
    // Only call the parent function if the category has actually changed.
    if (newCategory !== synopsisData.originalCategory) {
      onResubmitCategory(newCategory);
    }
    setIsEditing(false);
  };

  if (isEditing) {
    return (
      <div className="flex flex-col items-center w-full p-4 animate-fade-in">
        <h2 className="text-2xl font-bold mb-4 text-primary">Change Category</h2>
        <InputGroup
          initialValue={synopsisData.originalCategory}
          placeholder="Enter a new category..."
          onSubmit={handleResubmit}
        />
        <button 
          onClick={() => setIsEditing(false)} 
          className="mt-4 text-sm text-secondary hover:text-primary"
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className="w-full max-w-md mx-auto text-center animate-fade-in">
      <div className="group flex items-center justify-center gap-2 mb-4">
        <h1 className="text-3xl md:text-4xl font-extrabold text-primary">
          {synopsisData.quizTitle}
        </h1>
        <IconButton 
          Icon={EditIcon} 
          onClick={() => setIsEditing(true)}
          label="Edit quiz category"
        />
      </div>

      <div className="mb-6">
        <Image src={synopsisData.imageUrl} alt={synopsisData.quizTitle} className="w-full h-auto rounded-xl shadow-lg" />
      </div>

      <p className="text-secondary mb-8">
        {synopsisData.synopsis}
      </p>

      <IconButton
        Icon={ArrowIcon}
        onClick={onProceed}
        label="Start Quiz"
        className="w-16 h-16 mx-auto bg-primary text-white hover:bg-accent"
      />
    </div>
  );
});

export default SynopsisView;
