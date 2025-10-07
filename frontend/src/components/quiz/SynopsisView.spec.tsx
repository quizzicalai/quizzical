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
    expect(screen.getByLabelText('Zara')).toBeInTheDocument();
    // ensure we used the embedded list, not the prop
    expect(screen.queryByLabelText('Bram')).toBeNull();
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
    expect(screen.getByLabelText('Bram')).toBeInTheDocument();
    expect(screen.queryByLabelText('Ava')).toBeNull();
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
});
