import { useState } from 'react';
import { ArrowIcon } from '../../assets/icons/ArrowIcon';
import Spinner from './Spinner';

/**
 * A reusable component that combines a text input with an icon-based submit button.
 * It manages its own input state and displays a loading state.
 *
 * @param {object} props - The component props.
 * @param {string} props.placeholder - The placeholder text for the input field.
 * @param {Function} props.onSubmit - An async function to call with the input value when submitted.
 * @param {string} [props.initialValue=''] - The initial value for the input field.
 * @param {boolean} [props.isLoading=false] - Whether to display the loading state.
 */
function InputGroup({ placeholder, onSubmit, initialValue = '', isLoading = false }) {
  const [value, setValue] = useState(initialValue);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (value.trim() && !isLoading) {
      await onSubmit(value.trim());
    }
  };

  const isActive = value.trim() !== '' && !isLoading;

  return (
    <form onSubmit={handleSubmit} className="relative group w-full max-w-md">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        disabled={isLoading}
        className="w-full pl-6 pr-20 py-4 text-lg bg-white rounded-full border-2 border-muted focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent disabled:bg-muted transition-all"
      />
      <button
        type="submit"
        disabled={!isActive}
        aria-label="Submit"
        className={`absolute right-2 top-1/2 -translate-y-1/2 w-12 h-12 rounded-full flex items-center justify-center text-white transition-all ${
          isActive ? 'bg-primary hover:bg-accent' : 'bg-muted cursor-not-allowed'
        }`}
      >
        {isLoading ? (
          <Spinner size="h-6 w-6" color="border-white" />
        ) : (
          <ArrowIcon className="h-6 w-6" />
        )}
      </button>
    </form>
  );
}

export default InputGroup;
