// src/components/common/InputGroup.tsx
import React, { useCallback, useId, useRef, useState } from 'react';
import clsx from 'clsx';
import { Spinner } from './Spinner';
import { SendIcon } from '../../assets/icons/SendIcon'; // Updated import path

type InputGroupProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  placeholder?: string;
  helperText?: string;
  errorText?: string | null;
  minLength?: number;
  maxLength?: number;
  isSubmitting?: boolean;
  disabled?: boolean;
  id?: string;
  ariaLabel?: string;
  className?: string;
  inputClassName?: string;
  buttonClassName?: string;
};

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
  className,
  inputClassName,
  buttonClassName,
}: InputGroupProps) {
  const autoId = useId();
  const inputId = id || `input-${autoId}`;
  const helperId = helperText ? `helper-${autoId}` : undefined;
  const errorId = errorText ? `error-${autoId}` : undefined;

  const [localError, setLocalError] = useState<string | null>(null);
  const isComposingRef = useRef(false);

  const handleSubmit = useCallback(() => {
    if (isSubmitting || disabled) return;
    const trimmedValue = value.trim();
    if (minLength && trimmedValue.length > 0 && trimmedValue.length < minLength) {
      setLocalError(`Must be at least ${minLength} characters.`);
      return;
    }
    if (maxLength && trimmedValue.length > maxLength) {
      setLocalError(`Cannot exceed ${maxLength} characters.`);
      return;
    }
    if (trimmedValue) {
      onSubmit(trimmedValue.normalize('NFC'));
    }
  }, [isSubmitting, disabled, value, minLength, maxLength, onSubmit]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !isComposingRef.current) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const finalError = errorText || localError;
  const computedDisabled = isSubmitting || disabled;

  return (
    <div className={clsx('w-full max-w-lg', className)}>
      <div className="flex items-stretch">
        <input
          id={inputId}
          type="text"
          value={value}
          onChange={(e) => {
            setLocalError(null);
            onChange(e.target.value);
          }}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => isComposingRef.current = true}
          onCompositionEnd={() => isComposingRef.current = false}
          placeholder={placeholder}
          aria-label={ariaLabel}
          aria-invalid={!!finalError}
          aria-describedby={clsx(helperId, errorId) || undefined}
          disabled={computedDisabled}
          maxLength={maxLength}
          className={clsx(
            'flex-1 w-full px-4 py-3 rounded-l-md border outline-none transition-shadow',
            'bg-bg text-fg placeholder:text-muted',
            'focus:ring-2 focus:ring-ring focus:border-border',
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
            'inline-flex items-center justify-center px-4 rounded-r-md border border-l-0 transition-opacity',
            'bg-primary text-white',
            'hover:opacity-90 active:opacity-80',
            'focus:outline-none focus:ring-2 focus:ring-ring',
            (computedDisabled || !value.trim()) && 'opacity-60 cursor-not-allowed',
            buttonClassName
          )}
        >
          {isSubmitting ? <Spinner size="sm" /> : <SendIcon className="w-6 h-6" />}
        </button>
      </div>
      {helperText && !finalError && (
        <p id={helperId} className="mt-2 text-sm text-muted text-left">
          {helperText}
        </p>
      )}
      {finalError && (
        <p id={errorId} className="mt-2 text-sm text-red-600 text-left" role="alert">
          {finalError}
        </p>
      )}
    </div>
  );
}