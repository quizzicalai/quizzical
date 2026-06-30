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
import { loadAppConfig } from '../services/configService';
import { initializeApiService } from '../services/apiService';
import { validateAndNormalizeConfig } from '../utils/configValidation';
import { DEFAULT_APP_CONFIG } from '../config/defaultAppConfig';
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

/**
 * #16 (HITLIST-2026-06-30) — the normalized local default config used as the
 * INITIAL config so first paint is never blocked on the /config network RTT.
 * Built once at module load from DEFAULT_APP_CONFIG (run through the same
 * validate+merge+turnstile-align pipeline the backend payload goes through),
 * so the shape and the Turnstile flag are identical to a reconciled config.
 *
 * CRITICAL: DEFAULT_APP_CONFIG.features.turnstile === true, and
 * normalizeTurnstileFlag defaults the flag to true when unspecified, so the
 * Turnstile challenge is ON during this default window. A later background
 * reconcile that flips it OFF only RELAXES the gate; an OFF→ON flip can never
 * sneak a quiz start past the challenge because the start path always reads
 * the live (reconciled-or-default) flag, which is ON here.
 */
const INITIAL_DEFAULT_CONFIG: AppConfig =
  normalizeTurnstileFlag(validateAndNormalizeConfig(DEFAULT_APP_CONFIG)) as AppConfig;

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
  // #16 (HITLIST-2026-06-30) — start with the normalized local default config so
  // the app paints IMMEDIATELY instead of blocking the whole tree behind a
  // full-screen spinner until the /config RTT resolves. The real config is
  // fetched in the BACKGROUND and reconciled via setConfig on arrival.
  const [config, setConfig] = useState<AppConfig>(INITIAL_DEFAULT_CONFIG);
  // isLoading reflects only the background reconcile; we never gate rendering on
  // it (children render against the default config from the first frame). Kept
  // so consumers that want a "still syncing" affordance can read it.
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
      // 1) Fetch raw/partial config (in the background — children already paint)
      const raw = await loadAppConfig({ signal: controller.signal, timeoutMs: 10_000 });

      // 2) Validate/merge with defaults
      const validated = validateAndNormalizeConfig(raw) as any;

      // 3) Align the Turnstile flag (authoritative = features.turnstile)
      const aligned = normalizeTurnstileFlag(validated) as AppConfig;

      // Initialize API service with timeouts from config
      if (aligned?.apiTimeouts) {
        initializeApiService(aligned.apiTimeouts);
      }

      // 4) Reconcile: swap the default for the real config.
      setConfig(aligned);
    } catch (err: any) {
      if (err?.canceled === true || err?.name === 'AbortError') {
        if (IS_DEV) console.debug('[ConfigProvider] configuration load aborted (benign)');
        return;
      }
      // #16 — on a real fetch/validation failure we DO NOT blank the app or
      // show a full-screen error: we keep rendering against the normalized
      // local default config (Turnstile stays ON). `error` is still surfaced
      // for any consumer that wants to show a subtle "couldn't sync settings"
      // affordance, but it no longer blocks the tree.
      if (IS_DEV) console.error('[ConfigProvider] failed to load configuration; using local defaults:', err);
      setError('Failed to load application settings. Using defaults.');
    } finally {
      if (controllerRef.current === controller) {
        setIsLoading(false);
        controllerRef.current = null;
      }
    }
  }, []);

  // Initialize the API service from default timeouts on first mount so any
  // request that races the background reconcile still gets sane timeouts.
  const didInitApiRef = useRef(false);
  if (!didInitApiRef.current) {
    didInitApiRef.current = true;
    if (INITIAL_DEFAULT_CONFIG.apiTimeouts) {
      initializeApiService(INITIAL_DEFAULT_CONFIG.apiTimeouts);
    }
  }

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

  // Always render children — we have a usable config from the first frame.
  return (
    <ConfigContext.Provider value={value}>
      {children}
    </ConfigContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useConfig(): ConfigContextValue {
  const ctx = useContext(ConfigContext);
  if (ctx === null) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return ctx;
}

/** Optional ergonomic helper if you prefer: */
// eslint-disable-next-line react-refresh/only-export-components
export function useFeatures(): NonNullable<AppConfig['features']> {
  return useConfig().features;
}
