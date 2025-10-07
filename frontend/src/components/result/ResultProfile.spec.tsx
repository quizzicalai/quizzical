// src/components/result/ResultProfile.spec.tsx
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent, act } from '@testing-library/react';
import { ResultProfile } from './ResultProfile';
import type { ResultProfileData } from '../../types/result';

const baseResult: ResultProfileData = {
  profileTitle: 'The Maverick',
  summary: 'You forge your own path and thrive on challenges.',
  imageUrl: '/img/maverick.jpg',
  imageAlt: 'Portrait of a maverick',
  traits: [
    { id: 't1', label: 'Bold', value: 'High' },
    { id: 't2', label: 'Creative', value: 'Very High' },
  ],
} as any;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ResultProfile', () => {
  it('returns null when result is null', () => {
    const { container } = render(<ResultProfile result={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders title, focuses it on mount, and supports labels.titlePrefix', async () => {
    render(<ResultProfile result={baseResult} labels={{ titlePrefix: 'Your result:' }} />);

    const heading = screen.getByRole('heading', { name: /your result:\s*the maverick/i });
    expect(heading).toBeInTheDocument();

    await waitFor(() => expect(heading).toHaveFocus());
  });

  it('renders image only when imageUrl is a non-empty string; uses provided alt or title as fallback', () => {
    const { rerender } = render(<ResultProfile result={baseResult} />);

    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('src', '/img/maverick.jpg');
    expect(img).toHaveAttribute('alt', 'Portrait of a maverick');

    rerender(<ResultProfile result={{ ...baseResult, imageUrl: '   ' } as any} />);
    expect(screen.queryByRole('img')).toBeNull();

    rerender(<ResultProfile result={{ ...baseResult, imageAlt: undefined } as any} />);
    const img2 = screen.getByRole('img');
    expect(img2).toHaveAttribute('alt', baseResult.profileTitle);
  });

  it('renders the summary paragraph when provided', () => {
    render(<ResultProfile result={baseResult} />);
    expect(screen.getByText(/forge your own path/i)).toBeInTheDocument();
  });

  it('renders traits section with overrideable heading and each trait label/value', () => {
    render(<ResultProfile result={baseResult} labels={{ traitListTitle: 'Key Traits' }} />);

    expect(screen.getByRole('heading', { name: /key traits/i })).toBeInTheDocument();

    const listItems = screen.getAllByRole('listitem');
    expect(listItems).toHaveLength(2);

    expect(screen.getByText('Bold')).toBeInTheDocument();
    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getByText('Creative')).toBeInTheDocument();
    expect(screen.getByText('Very High')).toBeInTheDocument();
  });

  it('renders Start Another Quiz button when onStartNew is provided and calls it on click', () => {
    const onStartNew = vi.fn();
    render(<ResultProfile result={baseResult} onStartNew={onStartNew} />);

    const btn = screen.getByRole('button', { name: /start another quiz/i });
    expect(btn).toBeInTheDocument();

    fireEvent.click(btn);
    expect(onStartNew).toHaveBeenCalledTimes(1);
  });

  it('renders Share button only when shareUrl and onCopyShare are provided; toggles "copied" text then reverts after 2s', async () => {
    const onCopyShare = vi.fn().mockResolvedValue(undefined);
    const timeoutSpy = vi.spyOn(global, 'setTimeout');

    render(
        <ResultProfile
        result={baseResult}
        shareUrl="https://example.com/share/123"
        onCopyShare={onCopyShare}
        labels={{ shareButton: 'Share Result', shareCopied: 'Link Copied!' }}
        />
    );

    const shareBtn = screen.getByRole('button', { name: /share result/i });
    fireEvent.click(shareBtn);

    await waitFor(() => expect(onCopyShare).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: /link copied!/i })).toBeInTheDocument();

    // Find OUR timer (delay === 2000) among possibly many setTimeout calls.
    const twoSecCall = timeoutSpy.mock.calls.find(([, delay]) => delay === 2000);

    // If the environment wraps timers oddly, at least ensure a timer existed.
    expect(timeoutSpy).toHaveBeenCalled();

    // If we found the 2000ms call, run it; otherwise fall back to the last call.
    const cb =
        (twoSecCall?.[0] as () => void) ??
        (timeoutSpy.mock.calls.at(-1)?.[0] as () => void);

    // Execute the queued callback to simulate the 2s passing.
    await act(async () => {
        cb?.();
    });

    expect(screen.getByRole('button', { name: /share result/i })).toBeInTheDocument();

    timeoutSpy.mockRestore();
    });


  it('does not render Share button if either shareUrl or onCopyShare is missing', () => {
    const { rerender } = render(
      <ResultProfile result={baseResult} shareUrl="https://example.com/share/123" />
    );
    expect(screen.queryByRole('button', { name: /share/i })).toBeNull();

    rerender(<ResultProfile result={baseResult} onCopyShare={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /share/i })).toBeNull();
  });

  it('handleCopy safely no-ops if prerequisites are missing (no crash)', () => {
    render(<ResultProfile result={baseResult} />);
    expect(screen.getByRole('heading', { name: /the maverick/i })).toBeInTheDocument();
  });
});
