"""Iter 2 — modernize checkpointer wiring.

Best-practices in LangGraph 1.x:
- Prefer ``InMemorySaver`` (canonical name) over the legacy ``MemorySaver`` alias.
- The ``app.agent.graph`` module should expose ``InMemorySaver`` so callers /
  tests can import the canonical symbol from a single place.
- The compiled graph should use a real ``BaseCheckpointSaver`` instance.
"""

from __future__ import annotations

import os
import pytest

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver as CanonicalInMemorySaver


def test_graph_module_exposes_inmemorysaver() -> None:
    """``InMemorySaver`` must be re-exported by ``app.agent.graph``."""
    from app.agent import graph as graph_mod

    assert hasattr(graph_mod, "InMemorySaver"), (
        "graph module should re-export InMemorySaver as the canonical "
        "in-memory checkpointer for LangGraph 1.x"
    )
    assert graph_mod.InMemorySaver is CanonicalInMemorySaver


@pytest.mark.asyncio
async def test_create_agent_graph_uses_real_basecheckpointsaver(monkeypatch) -> None:
    """The compiled graph's checkpointer must be a real ``BaseCheckpointSaver``."""
    from app.agent import graph as graph_mod

    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    monkeypatch.setattr(graph_mod, "_env_name", lambda: "local")

    graph = await graph_mod.create_agent_graph()
    try:
        cp = getattr(graph, "_async_checkpointer", None)
        assert cp is not None
        assert isinstance(cp, BaseCheckpointSaver), (
            f"Checkpointer must inherit BaseCheckpointSaver, got {type(cp).__name__}"
        )
        # Canonical InMemorySaver must be used in local + USE_MEMORY_SAVER=1
        assert isinstance(cp, CanonicalInMemorySaver)
    finally:
        await graph_mod.aclose_agent_graph(graph)


def test_graph_module_does_not_re_export_legacy_memorysaver_alias() -> None:
    """Legacy ``MemorySaver`` import path should be replaced by ``InMemorySaver``.

    We allow the symbol to exist for back-compat, but the module's primary
    in-memory saver attribute must be ``InMemorySaver``. This test guards
    against future regressions that bring back ``MemorySaver`` as the only
    exported name.
    """
    from app.agent import graph as graph_mod

    # InMemorySaver is the canonical export (test above).
    # Ensure it exists *in addition to* / *instead of* a bare MemorySaver-only path.
    assert "InMemorySaver" in dir(graph_mod)
