// src/components/common/InputGroup.tsx
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { SendIcon } from '../../assets/icons/SendIcon';
import { InlineError } from './InlineError';
import { Spinner } from './Spinner';

interface ValidationMessages {
  minLength?: string;
  maxLength?: string;
  patternMismatch?: string;
}

interface InputGroupProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: (value: string) => void; // Made this prop optional
  placeholder?: string;
  errorText?: string | null;
  minLength?: number;
  maxLength?: number;
  isSubmitting?: boolean;
  ariaLabel: string;
  buttonText: string;
  validationMessages?: ValidationMessages;
}

export const InputGroup: React.FC<InputGroupProps> = ({
  value,
  onChange,
  onSubmit, // Can now be undefined
  placeholder,
  errorText,
  minLength,
  maxLength,
  isSubmitting = false,
  ariaLabel,
  buttonText,
  validationMessages = {},
}) => {
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Effect to clear validation error when API error is shown
  useEffect(() => {
    if (errorText) {
      setValidationError(null);
    }
  }, [errorText]);

  const handleValidation = useCallback(() => {
    if (inputRef.current) {
      const validity = inputRef.current.validity;
      if (validity.valueMissing) {
        // This case is handled by the browser's default behavior
        return true;
      }
      if (validity.tooShort) {
        setValidationError(validationMessages.minLength || `Minimum length is ${minLength}.`);
        return false;
      }
      if (validity.tooLong) {
        setValidationError(validationMessages.maxLength || `Maximum length is ${maxLength}.`);
        return false;
      }
      if (validity.patternMismatch) {
        setValidationError(validationMessages.patternMismatch || 'Invalid format.');
        return false;
      }
    }
    setValidationError(null);
    return true;
  }, [minLength, maxLength, validationMessages]);

  const handleSubmit = useCallback((event: React.FormEvent) => {
    event.preventDefault();
    if (isSubmitting) return;

    if (handleValidation() && onSubmit) { // Check if onSubmit exists before calling
      onSubmit(value);
    }
  }, [value, isSubmitting, handleValidation, onSubmit]);

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onChange(event.target.value);
    if (validationError) {
      setValidationError(null);
    }
  };

  const displayError = errorText || validationError;

  return (
    <div className="w-full max-w-lg mx-auto">
      <form onSubmit={handleSubmit} className="flex items-start space-x-2">
        <div className="flex-grow">
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={handleInputChange}
            placeholder={placeholder}
            className={`w-full px-4 py-3 border rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-focus ${
              displayError ? 'border-danger' : 'border-border'
            }`}
            required
            minLength={minLength}
            maxLength={maxLength}
            aria-label={ariaLabel}
            aria-invalid={!!displayError}
            aria-describedby={displayError ? 'input-error' : undefined}
            disabled={isSubmitting}
          />
        </div>
        <button
          type="submit"
          className="px-6 py-3 bg-primary text-primary-fg font-semibold rounded-md shadow-sm hover:bg-primary-hover focus:outline-none focus:ring-2 focus:ring-primary-focus disabled:bg-bg-disabled disabled:cursor-not-allowed flex items-center justify-center"
          disabled={isSubmitting || !value.trim()}
        >
          {isSubmitting ? <Spinner size="sm" /> : <SendIcon />}
          <span className="ml-2">{buttonText}</span>
        </button>
      </form>
      {displayError && (
        <div id="input-error" className="mt-2">
          <InlineError message={displayError} />
        </div>
      )}
    </div>
  );
};
