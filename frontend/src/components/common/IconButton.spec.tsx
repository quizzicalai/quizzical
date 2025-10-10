/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import IconButton from './IconButton';

afterEach(() => {
  cleanup();
});

const DummyIcon: React.FC<React.SVGProps<SVGSVGElement>> = (props) => (
  <svg data-testid="icon" {...props} />
);

describe('IconButton', () => {
  it('renders a button with the provided aria-label and the icon (md default size)', () => {
    render(<IconButton Icon={DummyIcon} label="Share this result" />);

    const btn = screen.getByRole('button', { name: 'Share this result' });
    expect(btn).toBeInTheDocument();

    const icon = screen.getByTestId('icon');
    expect(icon).toBeInTheDocument();

    // md => 22px width/height, and default icon classes
    expect(icon).toHaveAttribute('width', '22');
    expect(icon).toHaveAttribute('height', '22');
    expect(icon).toHaveClass('pointer-events-none');
  });

  it('calls onClick when enabled', () => {
    const onClick = vi.fn();
    render(<IconButton Icon={DummyIcon} onClick={onClick} label="Do it" />);

    const btn = screen.getByRole('button', { name: /do it/i });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not call onClick when disabled and sets disabled attribute', () => {
    const onClick = vi.fn();
    render(<IconButton Icon={DummyIcon} onClick={onClick} label="Disabled" disabled />);

    const btn = screen.getByRole('button', { name: /disabled/i }) as HTMLButtonElement;
    expect(btn).toBeDisabled();

    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('applies size classes and icon dimensions for sm and lg', () => {
    const { rerender } = render(<IconButton Icon={DummyIcon} label="Small" size="sm" />);

    let btn = screen.getByRole('button', { name: /small/i });
    let icon = screen.getByTestId('icon');
    expect(btn.className).toContain('w-10');
    expect(btn.className).toContain('h-10');
    expect(icon).toHaveAttribute('width', '18');
    expect(icon).toHaveAttribute('height', '18');

    rerender(<IconButton Icon={DummyIcon} label="Large" size="lg" />);
    btn = screen.getByRole('button', { name: /large/i });
    icon = screen.getByTestId('icon');
    expect(btn.className).toContain('w-12');
    expect(btn.className).toContain('h-12');
    expect(icon).toHaveAttribute('width', '26');
    expect(icon).toHaveAttribute('height', '26');
  });

  it('merges custom className onto the button and keeps base classes', () => {
    render(
      <IconButton
        Icon={DummyIcon}
        label="Styled"
        className="custom-class another-class"
      />
    );

    const btn = screen.getByRole('button', { name: /styled/i });
    // Updated base classes in component
    expect(btn.className).toContain('p-0');
    expect(btn.className).toContain('rounded-full');
    expect(btn.className).toContain('w-11');
    expect(btn.className).toContain('h-11');

    // User classes preserved
    expect(btn.className).toContain('custom-class');
    expect(btn.className).toContain('another-class');
  });

  it('uses variant token colors in inline styles when enabled and when disabled', () => {
    const { rerender } = render(
      <IconButton Icon={DummyIcon} label="Primary" variant="primary" />
    );
    let btn = screen.getByRole('button', { name: /primary/i });
    expect(btn).toHaveStyle({ backgroundColor: 'rgb(var(--color-primary, 79 70 229))' });

    rerender(
      <IconButton Icon={DummyIcon} label="Primary disabled" variant="primary" disabled />
    );
    btn = screen.getByRole('button', { name: /primary disabled/i });
    expect(btn).toHaveStyle({ backgroundColor: 'rgb(var(--color-border, 226 232 240))' });
  });

  it('passes iconClassName to the icon', () => {
    render(
      <IconButton
        Icon={DummyIcon}
        label="IconClass"
        iconClassName="fill-current"
      />
    );
    const icon = screen.getByTestId('icon');
    expect(icon).toHaveClass('pointer-events-none');
    expect(icon).toHaveClass('fill-current');
  });

  it('accepts and applies consumer style overrides', () => {
    render(
      <IconButton
        Icon={DummyIcon}
        label="Inline styled"
        style={{ fontSize: '20px' }}
      />
    );
    const btn = screen.getByRole('button', { name: /inline styled/i });
    expect(btn).toHaveStyle({ fontSize: '20px' });
    // background is still set by the component
    expect(btn).toHaveStyle({ backgroundColor: 'rgb(var(--color-primary, 79 70 229))' });
  });
});
