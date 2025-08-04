import React, { useCallback, useId, useRef, useState } from 'react';
import clsx from 'clsx';
import { Spinner } from './Spinner'; // Assuming a small spinner component exists

// A simple Send icon for the submit button
const SendIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    className="w-6 h-6"
    aria-hidden="true"
  >
    <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
  </svg>
);

export function InputGroup({
  value,
  onChange,
  onSubmit,
  placeholder,
  helperText,
  errorText,
  minLength,
  maxLength,
  isSubmitting = false,
  disabled = false,
  id,
  ariaLabel,
  ariaDescribedBy,
  name,
  autoFocus,
  autoComplete = 'off',
  inputMode = 'text',
  enterKeyHint = 'go',
  className,
  inputClassName,
  buttonClassName,
}) {
  const autoId = useId();
  const inputId = id || `input-${autoId}`;
  const helperId = helperText ? `helper-${autoId}` : undefined;
  const errorId = errorText ? `error-${autoId}` : undefined;

  const [localError, setLocalError] = useState(null);
  const isComposingRef = useRef(false);

  const validate = useCallback((val) => {
    const trimmed = val.trim();
    if (minLength && trimmed.length > 0 && trimmed.length < minLength) {
      return `Must be at least ${minLength} characters.`;
    }
    if (maxLength && trimmed.length > maxLength) {
      return `Cannot exceed ${maxLength} characters.`;
    }
    return null;
  }, [minLength, maxLength]);

  const handleChange = (e) => {
    setLocalError(null); // Clear local error on change
    onChange?.(e.target.value);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !isComposingRef.current) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = () => {
    if (isSubmitting || disabled) return;

    const validationError = validate(value);
    if (validationError) {
      setLocalError(validationError);
      return;
    }

    const normalizedValue = value.normalize('NFC').trim();
    if (normalizedValue) {
      onSubmit?.(normalizedValue);
    }
  };

  const computedDisabled = isSubmitting || disabled;
  const describedBy = clsx(ariaDescribedBy, helperId, errorId) || undefined;
  const finalError = errorText || localError;

  return (
    <div className={clsx('w-full max-w-lg', className)}>
      <div className="relative flex items-stretch">
        <input
          id={inputId}
          name={name}
          type="text"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onCompositionEnd={() => { isComposingRef.current = false; }}
          placeholder={placeholder}
          aria-label={ariaLabel}
          aria-invalid={!!finalError}
          aria-describedby={describedBy}
          disabled={computedDisabled}
          autoFocus={autoFocus}
          autoComplete={autoComplete}
          inputMode={inputMode}
          enterKeyHint={enterKeyHint}
          maxLength={maxLength}
          className={clsx(
            'flex-1 w-full px-4 py-3 rounded-l-lg border outline-none transition-shadow',
            'bg-bg text-fg placeholder:text-muted',
            'focus:ring-2 focus:ring-primary/50 focus:border-primary/60',
            computedDisabled && 'opacity-70 cursor-not-allowed',
            finalError && 'border-red-500 ring-red-500/50',
            inputClassName
          )}
        />
        <button
          type="button"
          onClick={handleSubmit}
          disabled={computedDisabled || !value.trim()}
          aria-label="Submit"
          className={clsx(
            'inline-flex items-center justify-center px-4 rounded-r-lg border border-l-0 transition-opacity',
            'bg-primary text-white',
            'hover:opacity-90 active:opacity-80',
            'focus:outline-none focus:ring-2 focus:ring-primary/50',
            (computedDisabled || !value.trim()) && 'opacity-60 cursor-not-allowed',
            buttonClassName
          )}
        >
          {isSubmitting ? <Spinner size="sm" /> : <SendIcon />}
        </button>
      </div>

      {helperText && !finalError && (
        <p id={helperId} className="mt-2 text-sm text-muted">
          {helperText}
        </p>
      )}
      {finalError && (
        <p id={errorId} className="mt-2 text-sm text-red-600" role="alert">
          {finalError}
        </p>
      )}
    </div>
  );
}