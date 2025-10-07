import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import Image from './Image';

const PLACEHOLDER =
  'https://placehold.co/600x400/e2e8f0/475569?text=Image+Not+Found';

afterEach(() => {
  cleanup();
});

describe('Image', () => {
  it('renders with provided src, alt, and merges className', () => {
    render(
      <Image
        src="https://example.com/pic.jpg"
        alt="example"
        className="rounded-lg shadow"
      />
    );

    const img = screen.getByRole<HTMLImageElement>('img', { name: /example/i });
    expect(img).toBeInTheDocument();
    expect(img.getAttribute('src')).toBe('https://example.com/pic.jpg');
    expect(img.getAttribute('alt')).toBe('example');

    // Base classes + user classes are present
    expect(img.className).toContain('transition-opacity');
    expect(img.className).toContain('duration-300');
    expect(img.className).toContain('opacity-0');
    expect(img.className).toContain('rounded-lg');
    expect(img.className).toContain('shadow');
  });

  it('onLoad removes the opacity-0 class (fades in)', () => {
    render(<Image src="/ok.png" alt="ok" />);
    const img = screen.getByRole<HTMLImageElement>('img', { name: 'ok' });

    // Initially has opacity-0
    expect(img.className).toContain('opacity-0');

    // Simulate load
    fireEvent.load(img);

    // The component removes opacity-0 on load
    expect(img.className).not.toContain('opacity-0');
  });

  it('onError swaps to placeholder src and clears onerror to prevent loops', () => {
    render(<Image src="/missing.png" alt="missing" />);
    const img = screen.getByRole<HTMLImageElement>('img', { name: 'missing' });

    // onError should set placeholder src
    fireEvent.error(img);

    expect(img.getAttribute('src')).toBe(PLACEHOLDER);

    // Ensure onerror cleared to avoid infinite loop if placeholder fails
    // Access the underlying DOM property:
    expect(img.onerror).toBeNull();
  });
});
