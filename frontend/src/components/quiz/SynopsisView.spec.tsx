/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { SynopsisView } from './SynopsisView';
import type { Synopsis, CharacterProfile } from '../../types/quiz';

afterEach(() => cleanup());

const baseSynopsis: Synopsis = {
  title: 'Epic Adventure',
  summary: 'A sweeping tale of courage and discovery.',
  imageUrl: '/syn.jpg',
  imageAlt: '', // decorative image (empty alt)
} as any;

const characters: CharacterProfile[] = [
  {
    name: 'Bram',
    shortDescription: 'Brilliant inventor.',
    profileText: 'Bram is a brilliant inventor who builds clever gadgets to solve tough problems.',
    imageUrl: '/bram.jpg',
  },
];

describe('SynopsisView', () => {
  it('returns null when synopsis is null', () => {
    const { container } = render(
      <SynopsisView synopsis={null} onProceed={() => {}} isLoading={false} inlineError={null} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders title, summary, and decorative image; focuses heading on mount', async () => {
    render(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );

    const heading = screen.getByRole('heading', { name: /epic adventure/i });
    expect(heading).toBeInTheDocument();
    await waitFor(() => expect(heading).toHaveFocus());

    expect(screen.getByText(/sweeping tale of courage/i)).toBeInTheDocument();

    // Decorative image => role is "presentation"
    const img = screen.getByRole('presentation');
    expect(img).toHaveAttribute('src', '/syn.jpg');
    expect(img).toHaveAttribute('alt', '');
  });

  it('prefers characters embedded in synopsis over the characters prop', () => {
    const synopsisWithChars = {
      ...baseSynopsis,
      characters: [{ name: 'Zara', shortDescription: 'Master strategist.', imageUrl: '/z.jpg' }],
    } as any;

    render(
      <SynopsisView
        synopsis={synopsisWithChars}
        characters={characters}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );

    expect(screen.getByRole('heading', { name: /epic adventure/i })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: /generated characters/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Zara' })).toBeInTheDocument();
    // ensure we used the embedded list, not the prop
    expect(screen.queryByRole('heading', { name: 'Bram' })).toBeNull();
  });

  it('uses the characters prop when synopsis.characters is missing/empty', () => {
    const synopsisNoChars = { ...baseSynopsis } as any;

    render(
      <SynopsisView
        synopsis={synopsisNoChars}
        characters={characters}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );

    const list = screen.getByRole('list', { name: /generated characters/i });
    expect(list).toBeInTheDocument();

    // Only "Bram" exists in the provided characters prop
    expect(screen.getByRole('heading', { name: 'Bram' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ava' })).toBeNull();
  });

  it('does not render the characters section when neither synopsis nor prop provides characters', () => {
    render(
      <SynopsisView
        synopsis={{ ...baseSynopsis, characters: [] } as any}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );

    expect(screen.queryByRole('list', { name: /generated characters/i })).toBeNull();
  });

  it('Start Quiz button calls onProceed and reflects loading state (disabled + aria-busy)', () => {
    const onProceed = vi.fn();
    const { rerender } = render(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={onProceed}
        isLoading={false}
        inlineError={null}
      />
    );

    const btn = screen.getByRole('button', { name: /start quiz/i });
    expect(btn).toBeEnabled();
    expect(btn).not.toHaveAttribute('aria-busy');

    fireEvent.click(btn);
    expect(onProceed).toHaveBeenCalledTimes(1);

    rerender(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={onProceed}
        isLoading={true}
        inlineError={null}
      />
    );

    const loadingBtn = screen.getByRole('button', { name: /loading/i });
    expect(loadingBtn).toBeDisabled();
    expect(loadingBtn).toHaveAttribute('aria-busy', 'true');
  });

  it('shows inline error message when inlineError is provided', () => {
    render(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={() => {}}
        isLoading={false}
        inlineError="Something went wrong"
      />
    );

    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent(/something went wrong/i);
  });

  // AC-PROD-R14-MIXED-IMG-1 — synopsis must render gracefully when the
  // character list contains a mix of "known" entries (with image URLs) and
  // "unknown" entries (imageUrl null/undefined, e.g. text-only precompute
  // packs whose FAL portraits were never baked). Each character row
  // renders its name + short description regardless of image state; only
  // the <img> is conditionally omitted via safeImageUrl. No broken icons,
  // no fallback text leaking the URL, no crash.
  it('renders mixed known + unknown character images without crashing', () => {
    const mixedSynopsis = {
      ...baseSynopsis,
      characters: [
        { name: 'Buzz Lightyear', shortDescription: 'Space ranger.', imageUrl: null },
        { name: 'Remy', shortDescription: 'Talented chef.', imageUrl: 'https://fal.media/files/x/y.jpg' },
        { name: 'Woody', shortDescription: 'Loyal sheriff.', imageUrl: undefined },
        { name: 'WALL-E', shortDescription: 'Curious robot.', imageUrl: 'https://v3b.fal.media/files/a/b.jpg' },
      ],
    } as any;

    render(
      <SynopsisView
        synopsis={mixedSynopsis}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );

    // All four names render
    expect(screen.getByRole('heading', { name: 'Buzz Lightyear' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Remy' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Woody' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'WALL-E' })).toBeInTheDocument();

    // Two character <img>s render (Remy + WALL-E); the synopsis hero (/syn.jpg) is the third.
    const imgs = Array.from(document.querySelectorAll('img')) as HTMLImageElement[];
    const charImgs = imgs.filter((i) => i.getAttribute('src')?.includes('fal.media'));
    expect(charImgs).toHaveLength(2);
    expect(charImgs.map((i) => i.getAttribute('src'))).toEqual([
      'https://fal.media/files/x/y.jpg',
      'https://v3b.fal.media/files/a/b.jpg',
    ]);

    // No "null", "undefined", or raw URL text leaked into the DOM.
    expect(screen.queryByText('null')).toBeNull();
    expect(screen.queryByText('undefined')).toBeNull();
  });

  // AC-PROD-R14-TITLE-1 — synopsis title size was reduced one Tailwind step
  // (was text-4xl sm:text-5xl) to feel more proportionate to the body and
  // give the hero image more visual weight.
  it('renders the title at text-3xl sm:text-4xl (one step smaller)', () => {
    render(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );
    const heading = screen.getByRole('heading', { name: /epic adventure/i });
    const tokens = heading.className.split(/\s+/);
    expect(tokens).toContain('text-3xl');
    expect(tokens).toContain('sm:text-4xl');
    expect(tokens).not.toContain('text-4xl');
    expect(tokens).not.toContain('sm:text-5xl');
  });

  // AC-PROD-R6-SYN-IMG-1 — hero image renders the source 16:9 art without
  // top/bottom cropping. The previous `h-64 object-cover` clipped the
  // 1024x576 source.
  it('renders the hero image with aspect-video (no h-64 crop)', () => {
    render(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );
    const img = document.querySelector('img[src="/syn.jpg"]') as HTMLImageElement | null;
    expect(img).not.toBeNull();
    expect(img!.className).toContain('aspect-video');
    expect(img!.className).not.toContain('h-64');
  });

  // UX audit M4: "Try another topic" link uses underline-offset-4 and
  // font-medium for a bolder, more intentional hover affordance.
  it('"Try another topic" button has correct typography classes (M4)', () => {
    const onStartOver = vi.fn();
    render(
      <SynopsisView
        synopsis={baseSynopsis}
        onProceed={() => {}}
        onStartOver={onStartOver}
        isLoading={false}
        inlineError={null}
      />
    );
    const btn = screen.getByRole('button', { name: /try another topic/i });
    expect(btn).toBeInTheDocument();
    expect(btn.className).toContain('underline-offset-4');
    expect(btn.className).toContain('font-medium');
    fireEvent.click(btn);
    expect(onStartOver).toHaveBeenCalledTimes(1);
  });

  // UX audit P3: character heading has `truncate` class so very long names
  // don't overflow on narrow screens.
  it('character name headings have truncate class (P3)', () => {
    const synWithChars = {
      ...baseSynopsis,
      characters: [
        { name: 'A Very Long Character Name That Could Overflow', shortDescription: 'Desc' },
      ],
    } as any;
    render(
      <SynopsisView
        synopsis={synWithChars}
        onProceed={() => {}}
        isLoading={false}
        inlineError={null}
      />
    );
    const heading = screen.getByRole('heading', {
      name: /a very long character name/i,
    });
    expect(heading.className).toContain('truncate');
  });
});
