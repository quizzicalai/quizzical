import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { SocialShareBar } from './SocialShareBar';

afterEach(() => cleanup());

const SHARE_URL = 'https://quafel.example/result/abc123';
const TITLE = "I'm The Baker — find out yours!";
const TEXT = 'Just took this fun quiz on Quafel.';
const IMG = 'https://fal.media/img/abc.png';

/**
 * The share UI is now a YouTube-style disclosure: a single trigger button
 * is always visible, and the preview + brand intents live inside a modal
 * dialog that opens on click. This helper flips the modal open so the
 * pre-existing assertions about preview/buttons keep working.
 */
function openShareModal() {
  fireEvent.click(screen.getByTestId('social-share-trigger'));
}

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
    openShareModal();
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
    openShareModal();
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
    openShareModal();
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
    openShareModal();
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

    openShareModal();
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

    openShareModal();
    fireEvent.click(screen.getByTestId('social-share-copy'));
    expect(await screen.findByText(/could not copy/i)).toBeInTheDocument();
  });

  it('Native share button only renders when navigator.share exists; clicking it calls navigator.share', async () => {
    // First render: no native share even after opening the modal.
    const { unmount } = render(
      <SocialShareBar shareUrl={SHARE_URL} shareTitle={TITLE} />,
    );
    openShareModal();
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

    openShareModal();
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
    delete (navigator as { share?: unknown }).share;
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
    openShareModal();
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
    openShareModal();
    const copyBtn = screen.getByTestId('social-share-copy');
    expect(copyBtn.getAttribute('aria-label')).toBe('Copy URL');
    const xLink = screen.getByTestId('social-share-x');
    expect(xLink.getAttribute('aria-label')).toBe('Tweet about it');
  });

  it('renders only a single trigger button by default; opens a dialog on click and closes via close button', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
        imageUrl={IMG}
      />,
    );

    // Closed by default: only the trigger is visible, no preview / icons.
    const trigger = screen.getByTestId('social-share-trigger');
    expect(trigger).toHaveTextContent(/share/i);
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('social-share-modal')).toBeNull();
    expect(screen.queryByTestId('social-share-preview')).toBeNull();
    expect(screen.queryByTestId('social-share-copy')).toBeNull();
    expect(screen.queryByTestId('social-share-x')).toBeNull();

    // Open via the trigger.
    fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    const modal = screen.getByTestId('social-share-modal');
    expect(modal.getAttribute('role')).toBe('dialog');
    expect(modal.getAttribute('aria-modal')).toBe('true');
    expect(screen.getByTestId('social-share-preview')).toBeInTheDocument();
    expect(screen.getByTestId('social-share-copy')).toBeInTheDocument();

    // Close via the dedicated close button.
    fireEvent.click(screen.getByTestId('social-share-close'));
    expect(screen.queryByTestId('social-share-modal')).toBeNull();
    expect(screen.queryByTestId('social-share-preview')).toBeNull();
  });

  // AC-UX-2026-05-02 — the modal must escape any ancestor `transform` /
  // `contain` stacking contexts (which were making the preview render
  // transparent on top of the result page). We do this with a portal
  // to document.body. If this regresses, the modal goes back to being
  // clipped to its parent and the preview goes transparent.
  it('portals the open modal to document.body (not nested under the trigger)', () => {
    const { container } = render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
        imageUrl={IMG}
      />,
    );
    fireEvent.click(screen.getByTestId('social-share-trigger'));

    const modal = screen.getByTestId('social-share-modal');
    // The modal must NOT be a descendant of the rendered component tree.
    expect(container.contains(modal)).toBe(false);
    // It must live directly under <body>.
    expect(document.body.contains(modal)).toBe(true);
  });

  // AC-UX-2026-05-03 — the dark overlay used to only cover the area
  // immediately above/below the modal card; it must now span the full
  // viewport so the modal reads as truly modal. We assert the backdrop
  // sits at `fixed inset-0 bg-black/60` with a higher z-index than the
  // page (>= 50).
  it('renders a full-viewport semi-transparent backdrop behind the share card', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
        imageUrl={IMG}
      />,
    );
    fireEvent.click(screen.getByTestId('social-share-trigger'));

    const backdrop = screen.getByTestId('social-share-backdrop');
    expect(backdrop.className).toMatch(/\babsolute\b/);
    expect(backdrop.className).toMatch(/\binset-0\b/);
    expect(backdrop.className).toMatch(/bg-black\/\d+/);

    // The wrapping modal is the element that establishes the
    // full-viewport positioning context. It must be fixed inset-0 and
    // stack above the rest of the app (z-50+).
    const modal = screen.getByTestId('social-share-modal');
    expect(modal.className).toMatch(/\bfixed\b/);
    expect(modal.className).toMatch(/\binset-0\b/);
    const zMatch = modal.className.match(/z-\[?(\d+)\]?/);
    expect(zMatch).not.toBeNull();
    expect(Number(zMatch![1])).toBeGreaterThanOrEqual(50);
  });

  // AC-UX-2026-05-02 — the preview card had no readable background
  // when the page's `--color-card` CSS var failed to resolve (e.g.,
  // dark-theme partial styling). We now pin an inline RGB fallback so
  // the preview is always opaque/legible.
  it('gives the modal panel and preview card opaque background fallbacks', () => {
    render(
      <SocialShareBar
        shareUrl={SHARE_URL}
        shareTitle={TITLE}
        shareText={TEXT}
        imageUrl={IMG}
      />,
    );
    fireEvent.click(screen.getByTestId('social-share-trigger'));

    const preview = screen.getByTestId('social-share-preview');
    // The preview card or one of its wrappers must carry an inline
    // backgroundColor so it never renders transparent on top of the
    // dark backdrop.
    const hasBg = (el: HTMLElement | null): boolean => {
      let cur: HTMLElement | null = el;
      while (cur) {
        if ((cur.style.backgroundColor || '').length > 0) return true;
        cur = cur.parentElement;
      }
      return false;
    };
    expect(hasBg(preview)).toBe(true);
  });
});
