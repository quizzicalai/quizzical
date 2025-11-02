/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Spinner } from '../components/common/Spinner';
import { InlineError } from '../components/common/InlineError';
import { loadAppConfig } from '../services/configService';
import { initializeApiService } from '../services/apiService';
import { validateAndNormalizeConfig } from '../utils/configValidation';
import type { AppConfig } from '../types/config';

const IS_DEV = import.meta.env.DEV === true;

type ConfigContextValue = {
  /** Full validated app config (or null while loading/failure). */
  config: AppConfig | null;
  /** Convenience: resolved features (always present with safe defaults). */
  features: NonNullable<AppConfig['features']>;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
};

const ConfigContext = createContext<ConfigContextValue | null>(null);

type ConfigProviderProps = {
  children: React.ReactNode;
};

/**
 * Ensure the Turnstile flag is present and aligned under both:
 *   - features.turnstile           (authoritative)
 *   - features.turnstileEnabled    (legacy mirror)
 *
 * Secure default: true (challenge ON) if not specified anywhere.
 */
function normalizeTurnstileFlag<T extends Record<string, any>>(cfg: T): T {
  const features = { ...(cfg?.features ?? {}) };
  const hasTurnstile = typeof features.turnstile === 'boolean';
  const hasEnabled = typeof features.turnstileEnabled === 'boolean';

  const value =
    hasTurnstile ? features.turnstile :
    hasEnabled ? features.turnstileEnabled :
    true;

  const siteKey =
    typeof features.turnstileSiteKey === 'string'
      ? features.turnstileSiteKey
      : features.turnstileSiteKey ?? undefined;

  return {
    ...cfg,
    features: {
      ...features,
      turnstile: value,
      turnstileEnabled: value, // keep legacy consumers in sync
      ...(siteKey !== undefined ? { turnstileSiteKey: siteKey } : {}),
    },
  };
}

/** Build a non-null features object for the context value. */
function deriveFeatures(config: AppConfig | null): NonNullable<AppConfig['features']> {
  // Secure defaults if config or features are missing (e.g., during load)
  const base = (config?.features ?? {}) as Record<string, unknown>;

  const hasTurnstile = typeof base.turnstile === 'boolean';
  const hasEnabled = typeof base.turnstileEnabled === 'boolean';
  const turnstile =
    hasTurnstile ? (base.turnstile as boolean)
      : hasEnabled ? (base.turnstileEnabled as boolean)
      : true;

  const out: NonNullable<AppConfig['features']> = {
    ...base,
    turnstile,
    turnstileEnabled: turnstile,
  } as NonNullable<AppConfig['features']>;

  return out;
}

export function ConfigProvider({ children }: ConfigProviderProps) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setIsLoading(true);
    setError(null);

    try {
      // 1) Fetch raw/partial config
      const raw = await loadAppConfig({ signal: controller.signal, timeoutMs: 10_000 });

      // 2) Validate/merge with defaults
      const validated = validateAndNormalizeConfig(raw) as any;

      // 3) Align the Turnstile flag (authoritative = features.turnstile)
      const aligned = normalizeTurnstileFlag(validated) as AppConfig;

      // Initialize API service with timeouts from config
      if (aligned?.apiTimeouts) {
        initializeApiService(aligned.apiTimeouts);
      }

      setConfig(aligned);
    } catch (err: any) {
      if (err?.canceled === true || err?.name === 'AbortError') {
        if (IS_DEV) console.debug('[ConfigProvider] configuration load aborted (benign)');
        return;
      }
      if (IS_DEV) console.error('[ConfigProvider] failed to load configuration:', err);
      setConfig(null);
      setError('Failed to load application settings. Please check your connection and try again.');
    } finally {
      if (controllerRef.current === controller) {
        setIsLoading(false);
        controllerRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    load();
    return () => {
      controllerRef.current?.abort();
    };
  }, [load]);

  const features = useMemo(() => deriveFeatures(config), [config]);

  const value: ConfigContextValue = useMemo(
    () => ({
      config,
      features, // ← exposed so callers can do: const { features } = useConfig();
      isLoading,
      error,
      reload: load,
    }),
    [config, features, isLoading, error, load]
  );

  return (
    <ConfigContext.Provider value={value}>
      {isLoading ? (
        <div className="flex h-screen items-center justify-center">
          <Spinner message="Loading Configuration..." />
        </div>
      ) : error ? (
        <InlineError message={error} onRetry={load} />
      ) : (
        children
      )}
    </ConfigContext.Provider>
  );
}

export function useConfig(): ConfigContextValue {
  const ctx = useContext(ConfigContext);
  if (ctx === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return ctx;
}

/** Optional ergonomic helper if you prefer: */
export function useFeatures(): NonNullable<AppConfig['features']> {
  return useConfig().features;
}
