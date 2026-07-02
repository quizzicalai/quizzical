/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { InfoTip } from './InfoTip';

afterEach(() => cleanup());

describe('InfoTip', () => {
  it('is closed by default and reveals the panel only when clicked', () => {
    render(<InfoTip label="How it works">quafel uses AI.</InfoTip>);
    const trigger = screen.getByTestId('info-tip-trigger');
    expect(trigger).toHaveAttribute('aria-label', 'How it works');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('info-tip-panel')).toBeNull();

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('info-tip-panel')).toHaveTextContent('quafel uses AI.');
  });

  it('closes on a second click', () => {
    render(<InfoTip label="Info">Body</InfoTip>);
    const trigger = screen.getByTestId('info-tip-trigger');
    fireEvent.click(trigger);
    expect(screen.getByTestId('info-tip-panel')).toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.queryByTestId('info-tip-panel')).toBeNull();
  });

  it('closes on Escape', () => {
    render(<InfoTip label="Info">Body</InfoTip>);
    fireEvent.click(screen.getByTestId('info-tip-trigger'));
    expect(screen.getByTestId('info-tip-panel')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('info-tip-panel')).toBeNull();
  });
});
