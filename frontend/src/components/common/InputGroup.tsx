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
  placeholder?: string;
  errorText?: string | null;
  minLength?: number;
  maxLength?: number;
  isSubmitting?: boolean;
  ariaLabel: string;
  buttonText: string;
  validationMessages?: ValidationMessages;
  formId?: string; // Optional form ID to associate button with parent form
}

export const InputGroup: React.FC<InputGroupProps> = ({
  value,
  onChange,
  placeholder,
  errorText,
  minLength,
  maxLength,
  isSubmitting = false,
  ariaLabel,
  buttonText,
  validationMessages = {},
  formId,
}) => {
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Effect to clear validation error when API error is shown
  useEffect(() => {
    if (errorText) {
      setValidationError(null);
    }
  }, [errorText]);

  const handleValidation = useCallback((): boolean => {
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

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onChange(event.target.value);
    if (validationError) {
      setValidationError(null);
    }
  };

  const handleInputBlur = useCallback(() => {
    handleValidation();
  }, [handleValidation]);

  const displayError = errorText || validationError;

  return (
    <div className="w-full max-w-lg mx-auto">
      <div className="flex items-start space-x-2">
        <div className="flex-grow">
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={handleInputChange}
            onBlur={handleInputBlur}
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
          form={formId} // Associate with parent form if ID provided
          className="px-6 py-3 bg-primary text-primary-fg font-semibold rounded-md shadow-sm hover:bg-primary-hover focus:outline-none focus:ring-2 focus:ring-primary-focus disabled:bg-bg-disabled disabled:cursor-not-allowed flex items-center justify-center"
          disabled={isSubmitting || !value.trim()}
        >
          {isSubmitting ? <Spinner size="sm" /> : <SendIcon />}
          <span className="ml-2">{buttonText}</span>
        </button>
      </div>
      {displayError && (
        <div id="input-error" className="mt-2">
          <InlineError message={displayError} />
        </div>
      )}
    </div>
  );
};