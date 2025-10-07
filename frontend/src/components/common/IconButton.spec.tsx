/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import IconButton from './IconButton';

afterEach(() => {
  cleanup();
});

describe('IconButton', () => {
  it('renders a button with the provided aria-label and the icon', () => {
    const seenProps: any[] = [];
    const DummyIcon: React.FC<any> = (props) => {
      seenProps.push(props);
      return <svg data-testid="icon" {...props} />;
    };

    const onClick = vi.fn();

    render(
      <IconButton
        Icon={DummyIcon}
        onClick={onClick}
        label="Share this result"
      />
    );

    const btn = screen.getByRole('button', { name: 'Share this result' });
    expect(btn).toBeInTheDocument();

    // Icon rendered inside
    const icon = screen.getByTestId('icon');
    expect(icon).toBeInTheDocument();

    // Icon props were passed correctly
    expect(seenProps[0]).toMatchObject({
      className: 'h-7 w-7',
      'aria-hidden': true,
    });
  });

  it('calls onClick when enabled', () => {
    const DummyIcon: React.FC<any> = (props) => <svg data-testid="icon" {...props} />;
    const onClick = vi.fn();

    render(<IconButton Icon={DummyIcon} onClick={onClick} label="Do it" />);

    const btn = screen.getByRole('button', { name: /do it/i });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not call onClick when disabled and sets disabled attribute', () => {
    const DummyIcon: React.FC<any> = (props) => <svg data-testid="icon" {...props} />;
    const onClick = vi.fn();

    render(
      <IconButton
        Icon={DummyIcon}
        onClick={onClick}
        label="Disabled"
        disabled
      />
    );

    const btn = screen.getByRole('button', { name: /disabled/i }) as HTMLButtonElement;
    expect(btn).toBeDisabled();

    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('merges custom className onto the button', () => {
    const DummyIcon: React.FC<any> = (props) => <svg data-testid="icon" {...props} />;
    const onClick = vi.fn();

    render(
      <IconButton
        Icon={DummyIcon}
        onClick={onClick}
        label="Styled"
        className="custom-class another-class"
      />
    );

    const btn = screen.getByRole('button', { name: /styled/i });
    expect(btn.className).toContain('p-2'); // base class is present
    expect(btn.className).toContain('custom-class');
    expect(btn.className).toContain('another-class');
  });
});
