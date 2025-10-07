/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { AnswerTile } from './AnswerTile';

// Mock the Logo icon so we can detect the fallback explicitly
vi.mock('../../assets/icons/Logo', () => ({
  Logo: (props: any) => <svg data-testid="logo-fallback" {...props} />,
}));

afterEach(() => cleanup());

const mkAnswer = (overrides: Partial<any> = {}) =>
  ({
    id: 'a1',
    text: 'Answer One',
    imageUrl: '/img1.jpg',
    imageAlt: 'Alt text',
    ...overrides,
  } as any);

describe('AnswerTile', () => {
  it('renders the image when imageUrl is present and no error occurred', () => {
    const onClick = vi.fn();
    const answer = mkAnswer();

    render(
      <AnswerTile answer={answer} onClick={onClick} disabled={false} isSelected={false} />
    );

    const img = screen.getByRole('img') as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.src).toContain('/img1.jpg');
    expect(img).toHaveAttribute('alt', 'Alt text');
    expect(screen.queryByTestId('logo-fallback')).toBeNull();
  });

  it('uses a generated alt when imageAlt is missing/empty', () => {
    const onClick = vi.fn();
    const answer = mkAnswer({ imageAlt: undefined });

    render(<AnswerTile answer={answer} onClick={onClick} />);

    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('alt', `Image for: ${answer.text}`);
  });

  it('falls back to the Logo when the image emits an error', () => {
    const onClick = vi.fn();
    const answer = mkAnswer();

    render(<AnswerTile answer={answer} onClick={onClick} />);

    const img = screen.getByRole('img');

    // Simulate the image failing to load
    fireEvent.error(img);

    // Now the Logo fallback should be shown, and the image should be gone
    expect(screen.getByTestId('logo-fallback')).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('falls back to the Logo when there is no imageUrl at all', () => {
    const onClick = vi.fn();
    const answer = mkAnswer({ imageUrl: undefined });

    render(<AnswerTile answer={answer} onClick={onClick} />);

    expect(screen.getByTestId('logo-fallback')).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('resets image error when answer.imageUrl changes (image shows again)', () => {
    const onClick = vi.fn();
    const answer1 = mkAnswer({ imageUrl: '/img1.jpg' });
    const { rerender } = render(<AnswerTile answer={answer1} onClick={onClick} />);

    // Cause error on first image
    const firstImg = screen.getByRole('img');
    fireEvent.error(firstImg);
    expect(screen.getByTestId('logo-fallback')).toBeInTheDocument();

    // Change answer -> different imageUrl
    const answer2 = mkAnswer({ imageUrl: '/img2.jpg' });
    rerender(<AnswerTile answer={answer2} onClick={onClick} />);

    const img2 = screen.getByRole('img') as HTMLImageElement;
    expect(img2.src).toContain('/img2.jpg');
    expect(screen.queryByTestId('logo-fallback')).toBeNull();
  });

  it('calls onClick with answer.id when not disabled', () => {
    const onClick = vi.fn();
    const answer = mkAnswer({ id: 'abc' });

    render(<AnswerTile answer={answer} onClick={onClick} disabled={false} />);

    fireEvent.click(screen.getByRole('button', { name: /select answer: answer one/i }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onClick).toHaveBeenCalledWith('abc');
  });

  it('does not call onClick when disabled', () => {
    const onClick = vi.fn();
    const answer = mkAnswer({ id: 'xyz' });

    render(<AnswerTile answer={answer} onClick={onClick} disabled />);

    const btn = screen.getByRole('button', { name: /select answer: answer one/i });
    expect(btn).toBeDisabled();

    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('reflects selected state via aria-pressed and selection styling class', () => {
    const onClick = vi.fn();
    const answer = mkAnswer();

    const { rerender } = render(
      <AnswerTile answer={answer} onClick={onClick} isSelected={false} />
    );

    const btn = screen.getByRole('button', { name: /select answer: answer one/i });

    // Initially not selected
    expect(btn).toHaveAttribute('aria-pressed', 'false');

    // Selected -> aria and styling class should reflect selection
    rerender(<AnswerTile answer={answer} onClick={onClick} isSelected />);
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(btn.className).toMatch(/border-primary/);

    // Selected + disabled -> still aria-pressed true and cursor-wait style present
    rerender(<AnswerTile answer={answer} onClick={onClick} isSelected disabled />);
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(btn.className).toMatch(/cursor-wait/);
  });
});
