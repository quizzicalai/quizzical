"""§9.7.8 — AC-LLM-CACHE-6: settings validation for LLMResponseCacheConfig."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import LLMGlobals, LLMResponseCacheConfig


class TestLLMResponseCacheConfigValidation:
    def test_defaults_are_safe(self):
        cfg = LLMResponseCacheConfig()
        assert cfg.enabled is False
        assert cfg.ttl_seconds == 3600
        assert cfg.namespace == "quizzical:llm"

    def test_ttl_zero_rejected(self):
        with pytest.raises(ValidationError):
            LLMResponseCacheConfig(ttl_seconds=0)

    def test_ttl_negative_rejected(self):
        with pytest.raises(ValidationError):
            LLMResponseCacheConfig(ttl_seconds=-1)

    def test_namespace_empty_rejected(self):
        with pytest.raises(ValidationError):
            LLMResponseCacheConfig(namespace="")

    def test_namespace_too_long_rejected(self):
        with pytest.raises(ValidationError):
            LLMResponseCacheConfig(namespace="a" * 65)

    @pytest.mark.parametrize("bad", ["has space", "has/slash", "has*", "naïve"])
    def test_namespace_invalid_chars_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            LLMResponseCacheConfig(namespace=bad)

    @pytest.mark.parametrize("ok", ["ns", "quizzical:llm", "ns_1-2:env", "A_B-C"])
    def test_namespace_valid_chars_accepted(self, ok: str):
        cfg = LLMResponseCacheConfig(namespace=ok)
        assert cfg.namespace == ok

    def test_llm_globals_default_includes_disabled_cache(self):
        g = LLMGlobals()
        assert g.response_cache.enabled is False
        assert g.response_cache.ttl_seconds == 3600
