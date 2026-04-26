"""Iter 9 — defaults in ``_analyze_topic_safe`` must not be clobbered.

The helper does::

    return {
        "outcome_kind": a.get("outcome_kind") or "types",
        ...
        **a,  # <-- spreads AFTER the defaults
    }

If ``analyze_topic`` returns ``{"outcome_kind": None}`` or
``{"outcome_kind": ""}``, the explicit default is silently overwritten by
the raw value from ``a`` because the unpacking happens last. This pins the
correct merge order: explicit defaults must win when the upstream value is
falsy, while still allowing extra keys from ``a`` to flow through.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.parametrize(
    "raw_analysis",
    [
        {"normalized_category": None, "outcome_kind": None, "creativity_mode": None,
         "intent": None, "domain": None, "names_only": None},
        {"normalized_category": "", "outcome_kind": "", "creativity_mode": "",
         "intent": "", "domain": "", "names_only": False},
    ],
)
def test_analyze_topic_safe_defaults_survive_extra_key_spread(monkeypatch, raw_analysis):
    from app.agent import graph as graph_mod

    monkeypatch.setattr(graph_mod, "analyze_topic", lambda _c: dict(raw_analysis))

    out = graph_mod._analyze_topic_safe("space exploration")

    assert out["normalized_category"] == "space exploration"
    assert out["outcome_kind"] == "types"
    assert out["creativity_mode"] == "balanced"
    assert out["intent"] == "identify"
    # ``domain`` falls back to "" when the raw value is empty/None.
    assert out["domain"] == ""
    assert out["names_only"] is False


def test_analyze_topic_safe_preserves_extra_keys_from_analyze_topic(monkeypatch):
    from app.agent import graph as graph_mod

    monkeypatch.setattr(
        graph_mod,
        "analyze_topic",
        lambda _c: {
            "normalized_category": "Cats",
            "outcome_kind": "types",
            "creativity_mode": "wild",
            "intent": "identify",
            "domain": "animals",
            "names_only": True,
            "shape": "person_or_character",   # extra key
            "score": 0.92,                    # extra key
        },
    )
    out: dict[str, Any] = graph_mod._analyze_topic_safe("cats")
    # Original computed values preserved.
    assert out["creativity_mode"] == "wild"
    assert out["domain"] == "animals"
    assert out["names_only"] is True
    # Extra upstream keys still propagate.
    assert out["shape"] == "person_or_character"
    assert out["score"] == 0.92


def test_analyze_topic_safe_falls_back_when_analyze_topic_raises(monkeypatch):
    from app.agent import graph as graph_mod

    def _boom(_c: str) -> dict:
        raise RuntimeError("nope")

    monkeypatch.setattr(graph_mod, "analyze_topic", _boom)
    out = graph_mod._analyze_topic_safe("unknown")
    assert out == {
        "normalized_category": "unknown",
        "outcome_kind": "types",
        "creativity_mode": "balanced",
        "names_only": False,
        "intent": "identify",
        "domain": "",
    }
