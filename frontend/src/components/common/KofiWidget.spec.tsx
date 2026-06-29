import { render, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KofiWidget } from './KofiWidget';
import { kofiUsername } from './kofiUsername';

// Controllable config mock — KofiWidget reads content?.donationUrl from here.
let __cfg: any = {};
vi.mock('../../context/ConfigContext', () => ({
  useConfig: () => ({ config: __cfg }),
}));

const SCRIPT_ID = 'kofi-overlay-widget';
const getScript = () => document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;

describe('kofiUsername', () => {
  it('extracts the username from a ko-fi.com URL', () => {
    expect(kofiUsername('https://ko-fi.com/quafel')).toBe('quafel');
    expect(kofiUsername('https://www.ko-fi.com/quafel/')).toBe('quafel');
  });
  it('returns null for non-ko-fi / empty URLs', () => {
    expect(kofiUsername('')).toBeNull();
    expect(kofiUsername(undefined)).toBeNull();
    expect(kofiUsername('https://buy.stripe.com/test_123')).toBeNull();
    expect(kofiUsername('https://evil.com/ko-fi.com/quafel')).toBeNull();
    expect(kofiUsername('http://ko-fi.com/quafel')).toBeNull(); // insecure scheme rejected
  });
});

describe('KofiWidget', () => {
  beforeEach(() => {
    __cfg = {};
    // Run the lazy idle-load synchronously so the effect injects immediately.
    (window as any).requestIdleCallback = (cb: () => void) => {
      cb();
      return 1;
    };
    (window as any).cancelIdleCallback = () => {};
    delete (window as any).kofiWidgetOverlay;
  });
  afterEach(() => {
    cleanup();
    getScript()?.remove();
    delete (window as any).kofiWidgetOverlay;
    delete (window as any).requestIdleCallback;
    delete (window as any).cancelIdleCallback;
    vi.restoreAllMocks();
  });

  it('renders no React DOM', () => {
    __cfg = { content: { donationUrl: 'https://ko-fi.com/quafel' } };
    const { container } = render(<KofiWidget />);
    expect(container.firstChild).toBeNull();
  });

  it('injects the external Ko-fi overlay script for a ko-fi.com donationUrl', () => {
    __cfg = { content: { donationUrl: 'https://ko-fi.com/quafel' } };
    render(<KofiWidget />);
    const s = getScript();
    expect(s).not.toBeNull();
    expect(s!.src).toContain('storage.ko-fi.com');
    expect(s!.async).toBe(true);
  });

  it('draws the floating widget with the derived username + brand colors on script load', () => {
    __cfg = { content: { donationUrl: 'https://ko-fi.com/quafel' } };
    const draw = vi.fn();
    render(<KofiWidget />);
    // Simulate the external script finishing loading.
    (window as any).kofiWidgetOverlay = { draw };
    getScript()!.dispatchEvent(new Event('load'));
    expect(draw).toHaveBeenCalled();
    const [usernameArg, opts] = draw.mock.calls[0];
    expect(usernameArg).toBe('quafel');
    expect(opts.type).toBe('floating-chat');
    expect(opts['floating-chat.donateButton.text']).toBe('Donate');
    expect(opts['floating-chat.donateButton.background-color']).toBe('#0079AE');
    expect(opts['floating-chat.donateButton.text-color']).toBe('#FFFFFF');
  });

  it('injects no script when no donationUrl is configured', () => {
    __cfg = { content: { donationUrl: '' } };
    render(<KofiWidget />);
    expect(getScript()).toBeNull();
  });

  it('does not activate for a non-ko-fi donationUrl', () => {
    __cfg = { content: { donationUrl: 'https://buy.stripe.com/test_123' } };
    render(<KofiWidget />);
    expect(getScript()).toBeNull();
  });
});
