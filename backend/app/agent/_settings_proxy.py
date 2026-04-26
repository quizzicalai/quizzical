"""Shared settings proxy used by agent modules.

Allows tests to override individual configuration attributes via simple
attribute assignment without mutating the real ``Settings`` singleton::

    from app.agent import graph as graph_mod
    graph_mod.settings.quiz.max_total_questions = 5  # test-only override

Lookups prefer overrides; otherwise they fall through to the wrapped base
settings object (or raise ``AttributeError`` if no base is available).
"""

from __future__ import annotations

from typing import Any, Optional


class SettingsProxy:
    """Read-through proxy with per-attribute override support."""

    __slots__ = ("_base", "_overrides")

    def __init__(self, base: Optional[Any]) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        base = object.__getattribute__(self, "_base")
        if base is None:
            raise AttributeError(name)
        return getattr(base, name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__getattribute__(self, "_overrides")[name] = value


__all__ = ["SettingsProxy"]
