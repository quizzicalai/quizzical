// frontend/tests/ct/mocks/ConfigContext.mock.ts
import { CONFIG_FIXTURE } from '../../fixtures/config.fixture';

type Cfg = typeof CONFIG_FIXTURE;
let _config: Cfg = CONFIG_FIXTURE;

export function useConfig() {
  return { config: _config, isLoading: false, error: null, reload: async () => {} };
}

// Optional test helpers if you need to override per-spec
export function __setTestConfig(c: Cfg) { _config = c; }
export function __getTestConfig() { return _config; }
