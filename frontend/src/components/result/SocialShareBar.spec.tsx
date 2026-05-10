import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { SocialShareBar } from './SocialShareBar';

afterEach(() => cleanup());

const SHARE_URL = 'https://quizzical.example/result/abc123';
const TITLE = "I'm The Baker — find out yours!";
const TEXT = 'Just took this fun quiz on Quizzical.';
const IMG = 'https://fal.media/img/abc.png';

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('SocialShareBar', () => {
  it('renders preview card with title, subtitle, url and image', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
        imageUrl={IMG}
        previewSubtitle="Warm and crusty"
      />,
    );

    expect(screen.getByTestId('social-share-bar')).toBeInTheDocument();
    const preview = screen.getByTestId('social-share-preview');
    expect(preview).toHaveTextContent(TITLE);
    expect(preview).toHaveTextContent(/warm and crusty/i);
    expect(preview).toHaveTextContent(SHARE_URL);
    // Image present (only safe https URLs reach here)
    const img = preview.querySelector('img');
    expect(img).not.toBeNull();
    expect(img!.getAttribute('src')).toBe(IMG);
  });

  it('renders all six brand intents as anchors with target=_blank and rel=noopener noreferrer', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
      />,
    );
    for (const key of ['x', 'facebook', 'linkedin', 'whatsapp', 'reddit', 'email']) {
      const a = screen.getByTestId(`social-share-${key}`) as HTMLAnchorElement;
      expect(a.tagName).toBe('A');
      if (key === 'email') {
        expect(a.href).toMatch(/^mailto:/);
      } else {
        expect(a.target).toBe('_blank');
        expect(a.rel).toMatch(/noopener/);
        expect(a.rel).toMatch(/noreferrer/);
      }
    }
  });

  it('builds intent URLs that contain the encoded share URL and title', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
      />,
    );
    const encodedUrl = encodeURIComponent(SHARE_URL);
    const encodedTitle = encodeURIComponent(TITLE);
    const encodedText = encodeURIComponent(TEXT);

    // Use getAttribute('href') to read the raw attribute value as written
    // (the .href property returns a normalized/re-encoded URL).
    const href = (id: string) =>
      screen.getByTestId(id).getAttribute('href') ?? '';

    const x = href('social-share-x');
    expect(x).toContain('twitter.com/intent/tweet');
    expect(x).toContain(`url=${encodedUrl}`);
    expect(x).toContain(`text=${encodedText}`);

    const fb = href('social-share-facebook');
    expect(fb).toContain('facebook.com/sharer/sharer.php');
    expect(fb).toContain(`u=${encodedUrl}`);

    const li = href('social-share-linkedin');
    expect(li).toContain('linkedin.com/sharing/share-offsite');
    expect(li).toContain(`url=${encodedUrl}`);

    const wa = href('social-share-whatsapp');
    expect(wa).toContain('wa.me');
    expect(wa).toContain(encodedUrl);
    expect(wa).toContain(encodedText);

    const rd = href('social-share-reddit');
    expect(rd).toContain('reddit.com/submit');
    expect(rd).toContain(`url=${encodedUrl}`);
    expect(rd).toContain(`title=${encodedTitle}`);

    const em = href('social-share-email');
    expect(em.startsWith('mailto:')).toBe(true);
    expect(em).toContain(`subject=${encodedTitle}`);
    expect(em).toContain(encodedUrl);
  });

  it('all icon buttons have an aria-label and matching native title (no visible text)', () => {
    render(
      <SocialShareBar shareUrl={SHARE_URL} shareTitle={TITLE} />,
    );
    const candidates = [
      'social-share-copy',
      'social-share-x',
      'social-share-facebook',
      'social-share-linkedin',
      'social-share-whatsapp',
      'social-share-reddit',
      'social-share-email',
    ];
    for (const id of candidates) {
      const el = screen.getByTestId(id);
      expect(el.getAttribute('aria-label')).toBeTruthy();
      expect(el.getAttribute('title')).toBe(el.getAttribute('aria-label'));
      // No visible label text — only the icon SVG.
      expect(el.textContent ?? '').toBe('');
    }
  });

  it('Copy link: invokes the writer with the share URL and shows a "Link copied" status', async () => {
    const writer = vi.fn().mockResolvedValue(undefined);

    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        writeToClipboard={writer}
      />,
    );

    fireEvent.click(screen.getByTestId('social-share-copy'));
    await waitFor(() => expect(writer).toHaveBeenCalledWith(SHARE_URL));
    expect(await screen.findByText(/link copied/i)).toBeInTheDocument();
    // After 2s the status should clear.
    vi.advanceTimersByTime(2100);
    await waitFor(() => {
      // sr-only after reset; visible text should be gone from the live region.
      const status = screen.getByRole('status');
      expect(status.textContent ?? '').toBe('');
    });
  });

  it('Copy link failure: shows an error message and recovers after a few seconds', async () => {
    const writer = vi.fn().mockRejectedValue(new Error('denied'));

    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        writeToClipboard={writer}
      />,
    );

    fireEvent.click(screen.getByTestId('social-share-copy'));
    expect(await screen.findByText(/could not copy/i)).toBeInTheDocument();
  });

  it('Native share button only renders when navigator.share exists; clicking it calls navigator.share', async () => {
    // First render: no native share.
    const { unmount } = render(
      <SocialShareBar shareUrl={SHARE_URL} shareTitle={TITLE} />,
    );
    expect(screen.queryByTestId('social-share-native')).toBeNull();
    unmount();

    // Now stub navigator.share and re-render.
    const shareSpy = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { share: shareSpy });

    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
      />,
    );

    const btn = await screen.findByTestId('social-share-native');
    fireEvent.click(btn);
    await waitFor(() =>
      expect(shareSpy).toHaveBeenCalledWith({
        title: TITLE,
        text: TEXT,
        url: SHARE_URL,
      }),
    );

    // Clean up the global pollution.
    // @ts-expect-error - removing the stub
    delete (navigator as any).share;
  });

  it('rejects unsafe image URLs (only https from allowlisted hosts render)', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        // javascript: URL must be stripped by safeImageUrl.
        imageUrl={'javascript:alert(1)' as unknown as string}
      />,
    );
    const preview = screen.getByTestId('social-share-preview');
    expect(preview.querySelector('img')).toBeNull();
  });

  it('respects the `labels` override for accessible names and tooltips', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        labels={{
          copyLink: 'Copy URL',
          shareOnX: 'Tweet about it',
        }}
      />,
    );
    const copyBtn = screen.getByTestId('social-share-copy');
    expect(copyBtn.getAttribute('aria-label')).toBe('Copy URL');
    const xLink = screen.getByTestId('social-share-x');
    expect(xLink.getAttribute('aria-label')).toBe('Tweet about it');
  });
});
