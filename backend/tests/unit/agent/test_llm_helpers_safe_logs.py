"""§19.3 AC-QUALITY-R2-LOG — _safe_* helpers in llm_helpers must emit a
structured debug event when they swallow an exception. This makes silent
fallbacks observable when DEBUG is enabled, without polluting prod logs.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.agent import llm_helpers


def _patch_logger(monkeypatch) -> MagicMock:
    """Replace llm_helpers.logger with a mock so we can assert on .debug() calls."""
    mock_logger = MagicMock()
    monkeypatch.setattr(llm_helpers, "logger", mock_logger)
    return mock_logger


def _debug_events(mock_logger: MagicMock) -> list[dict[str, Any]]:
    """Extract kwargs from each .debug(event_name, **kw) call."""
    out: list[dict[str, Any]] = []
    for call in mock_logger.debug.call_args_list:
        args, kwargs = call
        event = args[0] if args else kwargs.pop("event", None)
        out.append({"event": event, **kwargs})
    return out


def test_safe_len_logs_on_failure(monkeypatch):
    """AC-QUALITY-R2-LOG-1: _safe_len emits a debug event with helper name + obj_type."""

    class NoLen:
        pass

    mock = _patch_logger(monkeypatch)
    result = llm_helpers._safe_len(NoLen())

    assert result is None
    events = _debug_events(mock)
    assert any(
        e["event"] == "llm_helpers._safe_len.fallback" and e.get("obj_type") == "NoLen"
        for e in events
    ), f"missing debug event in {events!r}"


def test_cfg_get_logs_on_attribute_failure(monkeypatch):
    """AC-QUALITY-R2-LOG-1: _cfg_get emits a debug event when getattr explodes."""

    class Boom:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(f"no {name}")

    mock = _patch_logger(monkeypatch)
    result = llm_helpers._cfg_get(Boom(), "anything", default="DEF")

    assert result == "DEF"
    events = _debug_events(mock)
    assert any(
        e["event"] == "llm_helpers._cfg_get.fallback" and e.get("key") == "anything"
        for e in events
    ), f"missing debug event in {events!r}"


def test_deep_get_logs_on_step_failure(monkeypatch):
    """AC-QUALITY-R2-LOG-1: _deep_get emits a debug event identifying the failing step."""

    class Boom:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError(f"no {name}")

    mock = _patch_logger(monkeypatch)
    result = llm_helpers._deep_get(Boom(), ["a", "b"], default="DEF")

    assert result == "DEF"
    events = _debug_events(mock)
    assert any(
        e["event"] == "llm_helpers._deep_get.fallback" and e.get("step") == "a"
        for e in events
    ), f"missing debug event in {events!r}"
