// frontend/tests/ct/mocks/Turnstile.mock.tsx
import React from 'react';

type Props = {
  onVerify?: (token: string) => void;
  onError?: () => void;
  theme?: 'auto' | 'light' | 'dark';
};

export default function Turnstile({ onVerify }: Props) {
  return (
    <button
      data-testid="turnstile"
      aria-label="Turnstile (mock)"
      onClick={() => onVerify?.('ct-token')}
    >
      Verify (mock)
    </button>
  );
}
