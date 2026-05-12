"""§9.7.8 — AC-LLM-CACHE-1..3: lifespan wiring of LiteLLM Redis response cache."""
from __future__ import annotations

import importlib
import sys
import types

import pytest


@pytest.fixture()
def main_mod():
    import app.main as m
    return m


@pytest.fixture()
def reset_litellm_cache():
    """Always leave litellm.cache as None so other tests are unaffected."""
    import litellm
    saved = getattr(litellm, "cache", None)
    litellm.cache = None
    try:
        yield litellm
    finally:
        litellm.cache = saved


class _StubLogger:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def info(self, event: str, **kw):
        self.events.append(("info", event, kw))

    def warning(self, event: str, **kw):
        self.events.append(("warning", event, kw))

    def error(self, event: str, **kw):
        self.events.append(("error", event, kw))


class TestInitLLMCacheDisabled:
    """AC-LLM-CACHE-1 — cache disabled by default; no init, no litellm.cache."""

    def test_disabled_by_default_is_noop(self, main_mod, monkeypatch, reset_litellm_cache):
        # Settings.llm.response_cache.enabled is False by default.
        logger = _StubLogger()
        main_mod._init_llm_cache(logger, "local")
        assert reset_litellm_cache.cache is None
        assert all(evt[1] != "llm.cache.initialised" for evt in logger.events)


class TestInitLLMCacheEnabled:
    """AC-LLM-CACHE-2 — when enabled with reachable Redis, litellm.cache is set."""

    def test_enabled_constructs_cache(self, main_mod, monkeypatch, reset_litellm_cache):
        from app.core.config import settings, LLMResponseCacheConfig

        # Patch the response_cache config in-place for this test.
        original = settings.llm.response_cache
        settings.llm.response_cache = LLMResponseCacheConfig(
            enabled=True, ttl_seconds=120, namespace="qz:test"
        )

        constructed: dict = {}

        class FakeCache:
            def __init__(self, **kw):
                constructed.update(kw)

        # Stub litellm.caching.caching.Cache before _init_llm_cache imports it.
        fake_module = types.ModuleType("litellm.caching.caching")
        fake_module.Cache = FakeCache
        monkeypatch.setitem(sys.modules, "litellm.caching.caching", fake_module)

        # Provide a deterministic REDIS_URL.
        monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6390/0")
        # Ensure settings.REDIS_URL doesn't preempt env (it's None in tests).
        try:
            logger = _StubLogger()
            main_mod._init_llm_cache(logger, "local")

            import litellm
            assert isinstance(litellm.cache, FakeCache)
            assert constructed["type"] == "redis"
            assert constructed["host"] == "example.invalid"
            assert constructed["port"] == 6390
            assert constructed["namespace"] == "qz:test"
            assert "responses" in constructed["supported_call_types"]
            assert "acompletion" in constructed["supported_call_types"]

            init_events = [e for e in logger.events if e[1] == "llm.cache.initialised"]
            assert len(init_events) == 1
            assert init_events[0][2].get("namespace") == "qz:test"
            assert init_events[0][2].get("ttl_seconds") == 120
        finally:
            settings.llm.response_cache = original


class TestInitLLMCacheFailOpen:
    """AC-LLM-CACHE-3 — Redis/Cache failure does NOT crash startup; logs warning."""

    def test_construction_failure_is_fail_open(self, main_mod, monkeypatch, reset_litellm_cache):
        from app.core.config import settings, LLMResponseCacheConfig

        original = settings.llm.response_cache
        settings.llm.response_cache = LLMResponseCacheConfig(
            enabled=True, ttl_seconds=10, namespace="qz"
        )

        class BoomCache:
            def __init__(self, **kw):
                raise RuntimeError("redis down")

        fake_module = types.ModuleType("litellm.caching.caching")
        fake_module.Cache = BoomCache
        monkeypatch.setitem(sys.modules, "litellm.caching.caching", fake_module)

        try:
            logger = _StubLogger()
            # Must NOT raise.
            main_mod._init_llm_cache(logger, "local")
            import litellm
            assert litellm.cache is None
            warns = [e for e in logger.events if e[1] == "llm.cache.init_failed"]
            assert len(warns) == 1
            assert "redis down" in warns[0][2].get("error", "")
        finally:
            settings.llm.response_cache = original
