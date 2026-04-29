// frontend/src/hooks/useQuizMedia.spec.ts
//
// AC-FE-MEDIA-* — polling of GET /quiz/{id}/media:
//   - merges async-generated image URLs as they arrive
//   - never throws on transport failure
//   - stops polling once every expected image is present
//   - skips work when disabled or quizId missing
//
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

import { useQuizMedia } from './useQuizMedia';
import * as api from '../services/apiService';

vi.mock('../services/apiService', () => ({
  getQuizMedia: vi.fn(),
}));

const QUIZ_ID = '11111111-2222-3333-4444-555555555555';

beforeEach(() => {
  (api.getQuizMedia as any).mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useQuizMedia', () => {
  it('AC-FE-MEDIA-1: returns a snapshot from the first tick', async () => {
    (api.getQuizMedia as any).mockResolvedValue({
      quizId: QUIZ_ID,
      synopsisImageUrl: 'https://cdn/syn.jpg',
      resultImageUrl: null,
      characters: [{ name: 'Alpha', imageUrl: 'https://cdn/a.jpg' }],
    });

    const { result } = renderHook(() =>
      useQuizMedia(QUIZ_ID, {
        enabled: true,
        expectedCharacterNames: ['Alpha'],
      }),
    );

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(result.current.snapshot?.synopsisImageUrl).toBe('https://cdn/syn.jpg');
    expect(result.current.characterImageMap).toEqual({ Alpha: 'https://cdn/a.jpg' });
  });

  it('AC-FE-MEDIA-2: stops polling once all expected images resolve', async () => {
    vi.useFakeTimers();
    (api.getQuizMedia as any).mockResolvedValue({
      quizId: QUIZ_ID,
      synopsisImageUrl: 'https://cdn/syn.jpg',
      resultImageUrl: null,
      characters: [{ name: 'Alpha', imageUrl: 'https://cdn/a.jpg' }],
    });

    renderHook(() =>
      useQuizMedia(QUIZ_ID, {
        enabled: true,
        expectedCharacterNames: ['Alpha'],
        intervalMs: 1000,
      }),
    );

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const callsAfterFirst = (api.getQuizMedia as any).mock.calls.length;
    expect(callsAfterFirst).toBeGreaterThanOrEqual(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect((api.getQuizMedia as any).mock.calls.length).toBe(callsAfterFirst);
  });

  it('AC-FE-MEDIA-3: keeps polling while images are missing', async () => {
    vi.useFakeTimers();
    let call = 0;
    (api.getQuizMedia as any).mockImplementation(async () => {
      call += 1;
      return {
        quizId: QUIZ_ID,
        synopsisImageUrl: call >= 2 ? 'https://cdn/syn.jpg' : null,
        resultImageUrl: null,
        characters: [{ name: 'Alpha', imageUrl: call >= 2 ? 'https://cdn/a.jpg' : null }],
      };
    });

    const { result } = renderHook(() =>
      useQuizMedia(QUIZ_ID, {
        enabled: true,
        expectedCharacterNames: ['Alpha'],
        intervalMs: 100,
      }),
    );

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(150); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(result.current.snapshot?.synopsisImageUrl).toBe('https://cdn/syn.jpg');
    expect(result.current.characterImageMap.Alpha).toBe('https://cdn/a.jpg');
  });

  it('AC-FE-MEDIA-4: tolerates network failures without crashing', async () => {
    (api.getQuizMedia as any).mockRejectedValue(new Error('boom'));

    const { result } = renderHook(() =>
      useQuizMedia(QUIZ_ID, {
        enabled: true,
        expectedCharacterNames: ['Alpha'],
        intervalMs: 50,
        maxDurationMs: 200,
      }),
    );

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect((api.getQuizMedia as any).mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(result.current.snapshot).toBeNull();
  });

  it('AC-FE-MEDIA-5: disabled hook makes no requests', async () => {
    vi.useFakeTimers();
    renderHook(() =>
      useQuizMedia(QUIZ_ID, { enabled: false, expectedCharacterNames: ['Alpha'] }),
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect((api.getQuizMedia as any).mock.calls.length).toBe(0);
  });

  it('AC-FE-MEDIA-6: missing quizId makes no requests', async () => {
    vi.useFakeTimers();
    renderHook(() =>
      useQuizMedia(null, { enabled: true, expectedCharacterNames: [] }),
    );

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect((api.getQuizMedia as any).mock.calls.length).toBe(0);
  });
});
