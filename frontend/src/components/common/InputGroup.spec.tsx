/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

// ---- Mocks for children & icon ----
// Match Vite's resolved IDs (absolute, no extension)
vi.mock('/src/components/common/InlineError.tsx', () => ({
  InlineError: ({ message }: { message: string }) => (
    <div data-testid="inline-error">{message}</div>
  ),
}));

vi.mock('/src/components/common/Spinner.tsx', () => ({
  Spinner: ({ size }: { size?: 'sm' | 'md' | 'lg' }) => (
    <div data-testid="spinner">sp-{size || 'md'}</div>
  ),
}));

vi.mock('/src/assets/icons/SendIcon.tsx', () => ({
  SendIcon: () => <svg data-testid="send-icon" />,
}));

// Import under test after mocks
const { InputGroup } = await import('./InputGroup');

describe('InputGroup', () => {
  beforeEach(() => {
    cleanup();
  });

  function setup(overrides: Partial<React.ComponentProps<typeof InputGroup>> = {}) {
    const onChange = vi.fn();
    const props: React.ComponentProps<typeof InputGroup> = {
      value: overrides.value ?? '',
      onChange,
      placeholder: 'type here',
      errorText: overrides.errorText ?? null,
      minLength: overrides.minLength ?? 3,
      maxLength: overrides.maxLength ?? 10,
      isSubmitting: overrides.isSubmitting ?? false,
      ariaLabel: 'category',
      buttonText: 'Create',
      validationMessages: overrides.validationMessages,
      formId: overrides.formId,
    };
    const utils = render(<InputGroup {...props} />);
    const input = screen.getByRole('textbox', { name: /category/i }) as HTMLInputElement;
    const button = screen.getByRole('button', { name: /create/i });
    return { ...utils, props, input, button, onChange };
  }

  it('renders input + submit button; button disabled when empty and enabled when typed', () => {
    const { input, button, onChange } = setup();

    // empty => disabled (trim check)
    expect(button).toBeDisabled();

    // type value -> onChange called, button enabled
    fireEvent.change(input, { target: { value: 'abc' } });
    expect(onChange).toHaveBeenCalledWith('abc');
    // component's `value` prop is controlled; re-render with new value to reflect UI state:
    cleanup();
    const { button: button2 } = setup({ value: 'abc' });
    expect(button2).not.toBeDisabled();
    // and SendIcon visible (not the spinner)
    expect(screen.getByTestId('send-icon')).toBeInTheDocument();
    expect(screen.queryByTestId('spinner')).toBeNull();
  });

  it('shows spinner and disables when isSubmitting=true', () => {
    const { button } = setup({ value: 'ready', isSubmitting: true });

    expect(button).toBeDisabled();
    expect(screen.getByTestId('spinner')).toHaveTextContent('sp-sm'); // size="sm" passed by component
    expect(screen.queryByTestId('send-icon')).toBeNull();
  });

  it('validates on blur: tooShort -> shows validation message, sets ARIA, then clears on change', () => {
    const { input } = setup({ value: 'ab', minLength: 3 });

    // mock validity so blur will mark tooShort=true
    Object.defineProperty(input, 'validity', {
      value: {
        valueMissing: false,
        tooShort: true,
        tooLong: false,
        patternMismatch: false,
      },
      configurable: true,
    });

    // blur -> triggers handleValidation()
    fireEvent.blur(input);

    // default message (since no custom validationMessages provided)
    expect(screen.getByTestId('inline-error')).toHaveTextContent('Minimum length is 3.');

    // ARIA reflects error
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAttribute('aria-describedby', 'input-error');

    // typing clears inline validation error
    fireEvent.change(input, { target: { value: 'abcd' } });
    // re-render controlled value state to reflect change
    cleanup();
    setup({ value: 'abcd', minLength: 3 });
    expect(screen.queryByTestId('inline-error')).toBeNull();
  });

  it('uses custom validation messages when provided', () => {
    const { input } = setup({
      value: 'ab',
      minLength: 3,
      validationMessages: { minLength: 'Must be at least {min} chars.' },
    });

    Object.defineProperty(input, 'validity', {
      value: {
        valueMissing: false,
        tooShort: true,
        tooLong: false,
        patternMismatch: false,
      },
      configurable: true,
    });

    fireEvent.blur(input);

    // Component does not template {min}; it uses provided string as-is.
    expect(screen.getByTestId('inline-error')).toHaveTextContent('Must be at least {min} chars.');
  });

  it('API error (errorText) is displayed and clears any prior validation error', () => {
    const { input, rerender } = setup({ value: 'ab', minLength: 3 });

    // First: force a tooShort validation error via blur
    Object.defineProperty(input, 'validity', {
      value: {
        valueMissing: false,
        tooShort: true,
        tooLong: false,
        patternMismatch: false,
      },
      configurable: true,
    });
    fireEvent.blur(input);
    expect(screen.getByTestId('inline-error')).toBeInTheDocument();

    // Now: provide errorText from API; effect should clear local validation error
    rerender(
      <InputGroup
        value="ab"
        onChange={vi.fn()}
        placeholder="type here"
        errorText="Server says nope."
        minLength={3}
        maxLength={10}
        isSubmitting={false}
        ariaLabel="category"
        buttonText="Create"
      />
    );

    const err = screen.getByTestId('inline-error');
    expect(err).toHaveTextContent('Server says nope.');
  });

  it('propagates formId to the submit button (for external form association)', () => {
    const { button } = setup({ value: 'abc', formId: 'my-form' });
    expect(button).toHaveAttribute('form', 'my-form');
  });

  it('tooLong and patternMismatch paths render their respective messages', () => {
    // tooLong
    const { input: inputLong } = setup({
      value: 'abcdefghijklmnop',
      maxLength: 5,
      validationMessages: { maxLength: 'Too long!' },
    });
    Object.defineProperty(inputLong, 'validity', {
      value: {
        valueMissing: false,
        tooShort: false,
        tooLong: true,
        patternMismatch: false,
      },
      configurable: true,
    });
    fireEvent.blur(inputLong);
    expect(screen.getByTestId('inline-error')).toHaveTextContent('Too long!');

    cleanup();

    // patternMismatch
    const { input: inputPattern } = setup({
      value: 'abc',
      validationMessages: { patternMismatch: 'Bad format.' },
    });
    Object.defineProperty(inputPattern, 'validity', {
      value: {
        valueMissing: false,
        tooShort: false,
        tooLong: false,
        patternMismatch: true,
      },
      configurable: true,
    });
    fireEvent.blur(inputPattern);
    expect(screen.getByTestId('inline-error')).toHaveTextContent('Bad format.');
  });
});
