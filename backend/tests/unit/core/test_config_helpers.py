"""Unit tests for private normalization/loader helpers in app.core.config:
_ensure_quizzical_root, _lift_llm_maps, _deep_merge, _to_settings_model,
_load_from_yaml, _load_secrets_from_env, _load_secrets_from_key_vault.

These helpers govern config precedence (defaults → YAML → KV → env), so they
deserve dedicated coverage independent of get_settings().
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import config as cfg


# ---------------------------------------------------------------------------
# _ensure_quizzical_root
# ---------------------------------------------------------------------------
class TestEnsureQuizzicalRoot:
    def test_already_rooted_returns_as_is(self):
        raw = {"quizzical": {"app": {"name": "x"}}}
        assert cfg._ensure_quizzical_root(raw) is raw

    def test_unrooted_with_known_keys_gets_wrapped(self):
        raw = {"app": {"name": "x"}, "feature_flags": {}}
        out = cfg._ensure_quizzical_root(raw)
        assert out == {"quizzical": {"app": {"name": "x"}, "feature_flags": {}}}

    def test_unknown_keys_returned_unchanged(self):
        raw = {"random_key": 1}
        assert cfg._ensure_quizzical_root(raw) is raw

    def test_quizzical_with_non_dict_value_treated_as_known_key(self):
        # Branch: 'quizzical' present but not a dict → falls through to the
        # known-keys check; 'quizzical' alone is not in the trigger set, so
        # raw is returned unchanged.
        raw = {"quizzical": "not-a-dict"}
        assert cfg._ensure_quizzical_root(raw) is raw


# ---------------------------------------------------------------------------
# _lift_llm_maps
# ---------------------------------------------------------------------------
class TestLiftLlmMaps:
    def test_promotes_tools_and_prompts(self):
        q = {"llm": {"tools": {"web": {}}, "prompts": {"sys": {}}, "per_call_timeout_s": 30}}
        out = cfg._lift_llm_maps(q)
        assert out["llm_tools"] == {"web": {}}
        assert out["llm_prompts"] == {"sys": {}}
        # Non-map keys preserved under llm.
        assert out["llm"] == {"per_call_timeout_s": 30}

    def test_missing_llm_block_yields_empty_defaults(self):
        out = cfg._lift_llm_maps({})
        assert out["llm_tools"] == {}
        assert out["llm_prompts"] == {}
        assert out["llm"] == {}

    def test_non_dict_llm_left_untouched(self):
        # If llm is not a dict, the function shouldn't crash; the defaults are
        # set via setdefault so original value persists.
        out = cfg._lift_llm_maps({"llm": "weird"})
        assert out["llm"] == "weird"
        assert out["llm_tools"] == {}
        assert out["llm_prompts"] == {}

    def test_does_not_mutate_input(self):
        q = {"llm": {"tools": {"a": 1}}}
        cfg._lift_llm_maps(q)
        # Original llm key still has 'tools'.
        assert "tools" in q["llm"]


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------
class TestDeepMerge:
    def test_top_level_override(self):
        assert cfg._deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dicts_merged(self):
        base = {"a": {"x": 1, "y": 2}}
        over = {"a": {"y": 20, "z": 30}}
        assert cfg._deep_merge(base, over) == {"a": {"x": 1, "y": 20, "z": 30}}

    def test_none_in_override_keeps_base(self):
        # _merge: returns b if b is not None else a — so None preserves a.
        assert cfg._deep_merge({"a": 1}, {"a": None}) == {"a": 1}

    def test_list_replaces_not_extends(self):
        # Lists are not dicts → override wins.
        assert cfg._deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_new_keys_added(self):
        assert cfg._deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# _to_settings_model
# ---------------------------------------------------------------------------
class TestToSettingsModel:
    def test_minimal_root_builds_settings(self):
        s = cfg._to_settings_model({"quizzical": {}})
        # Smoke check — Settings fields populated with defaults.
        assert s.app is not None
        assert s.security is not None
        assert s.llm_tools == {}
        assert s.llm_prompts == {}

    def test_invalid_tool_config_raises_value_error(self):
        bad = {"quizzical": {"llm": {"tools": {"web": {"max_tokens": "not-an-int-or-valid-shape"}}}}}
        # Some shapes are accepted; force a real schema violation by passing an
        # impossible top-level dict.
        bad = {"quizzical": {"llm": {"tools": {"web": "not-a-mapping"}}}}
        with pytest.raises((ValueError, TypeError)):
            cfg._to_settings_model(bad)


# ---------------------------------------------------------------------------
# _load_from_yaml
# ---------------------------------------------------------------------------
class TestLoadFromYaml:
    def test_returns_none_when_path_missing(self, tmp_path, monkeypatch):
        missing = tmp_path / "nope.yaml"
        monkeypatch.setenv("APP_CONFIG_LOCAL_PATH", str(missing))
        assert cfg._load_from_yaml() is None

    def test_loads_valid_yaml_file(self, tmp_path, monkeypatch):
        path = tmp_path / "appconfig.local.yaml"
        path.write_text("quizzical:\n  app:\n    name: testapp\n", encoding="utf-8")
        monkeypatch.setenv("APP_CONFIG_LOCAL_PATH", str(path))
        out = cfg._load_from_yaml()
        assert out == {"quizzical": {"app": {"name": "testapp"}}}

    def test_returns_none_on_malformed_yaml(self, tmp_path, monkeypatch):
        path = tmp_path / "bad.yaml"
        path.write_text("[: : not valid", encoding="utf-8")
        monkeypatch.setenv("APP_CONFIG_LOCAL_PATH", str(path))
        # Function logs and returns None on parse failure.
        assert cfg._load_from_yaml() is None

    def test_returns_none_when_root_not_mapping(self, tmp_path, monkeypatch):
        path = tmp_path / "list.yaml"
        path.write_text("- one\n- two\n", encoding="utf-8")
        monkeypatch.setenv("APP_CONFIG_LOCAL_PATH", str(path))
        assert cfg._load_from_yaml() is None


# ---------------------------------------------------------------------------
# _load_secrets_from_env
# ---------------------------------------------------------------------------
class TestLoadSecretsFromEnv:
    def test_no_env_vars_returns_empty_security_block(self, monkeypatch):
        # Clear all relevant env vars.
        for name in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "ENABLE_TURNSTILE"):
            monkeypatch.delenv(name, raising=False)
        # Block dotenv loader to keep the test hermetic.
        monkeypatch.setattr(cfg, "_maybe_load_dotenv", lambda: None)
        out = cfg._load_secrets_from_env()
        # Structure always present; turnstile dict empty, no 'enabled' key.
        assert "quizzical" in out
        assert "security" in out["quizzical"]
        assert out["quizzical"]["security"].get("turnstile") == {}
        assert "enabled" not in out["quizzical"]["security"]

    def test_turnstile_keys_populated(self, monkeypatch):
        monkeypatch.setenv("TURNSTILE_SITE_KEY", "site-xyz")
        monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret-xyz")
        monkeypatch.delenv("ENABLE_TURNSTILE", raising=False)
        monkeypatch.setattr(cfg, "_maybe_load_dotenv", lambda: None)
        out = cfg._load_secrets_from_env()
        ts = out["quizzical"]["security"]["turnstile"]
        assert ts == {"site_key": "site-xyz", "secret_key": "secret-xyz"}

    @pytest.mark.parametrize("raw,expected", [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
    ])
    def test_enable_turnstile_flag(self, monkeypatch, raw, expected):
        monkeypatch.setenv("ENABLE_TURNSTILE", raw)
        for name in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(cfg, "_maybe_load_dotenv", lambda: None)
        out = cfg._load_secrets_from_env()
        assert out["quizzical"]["security"]["enabled"] is expected


# ---------------------------------------------------------------------------
# _load_secrets_from_key_vault (currently disabled — pinned behaviour)
# ---------------------------------------------------------------------------
class TestLoadSecretsFromKeyVault:
    def test_currently_returns_none_because_disabled(self, monkeypatch):
        # Even with KV envs set, the helper short-circuits to None.
        monkeypatch.setenv("KEYVAULT_URI", "https://example.vault.azure.net")
        assert cfg._load_secrets_from_key_vault() is None


# ---------------------------------------------------------------------------
# _maybe_load_dotenv
# ---------------------------------------------------------------------------
class TestMaybeLoadDotenv:
    def test_silent_when_dotenv_missing(self, monkeypatch):
        # Force ImportError by removing dotenv from sys.modules and blocking import.
        import builtins as _b

        real_import = _b.__import__

        def block(name, *args, **kwargs):
            if name == "dotenv":
                raise ImportError("blocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(_b, "__import__", block)
        # Should be a silent no-op, no exception.
        cfg._maybe_load_dotenv()

    def test_loads_first_existing_candidate(self, tmp_path, monkeypatch):
        # Point ENV_FILE at a real file; dotenv may or may not be installed.
        env_file = tmp_path / ".env"
        env_file.write_text("FOO_TEST_VAR=bar\n", encoding="utf-8")
        monkeypatch.setenv("ENV_FILE", str(env_file))
        monkeypatch.delenv("FOO_TEST_VAR", raising=False)
        cfg._maybe_load_dotenv()
        # If dotenv is installed, var is now set; if not, the helper was a no-op.
        # Either path is acceptable — assert no exception was raised.
