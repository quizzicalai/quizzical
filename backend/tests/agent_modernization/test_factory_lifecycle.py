"""Iter 4 — verify the compile-and-attach lifecycle on LangGraph 1.x.

The agent's ``create_agent_graph()`` factory attaches the checkpointer (and
when applicable the AsyncRedisSaver context manager) to the compiled graph
so ``aclose_agent_graph()`` can release them on shutdown. LangGraph 1.x
changed how ``CompiledStateGraph`` is constructed; this test guards that
the lifecycle hooks remain reachable.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_factory_attaches_checkpointer_for_shutdown(monkeypatch) -> None:
    from app.agent import graph as graph_mod

    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    monkeypatch.setattr(graph_mod, "_env_name", lambda: "local")

    g = await graph_mod.create_agent_graph()
    try:
        # Both attributes must be readable so aclose can release resources.
        assert hasattr(g, "_async_checkpointer")
        # In memory mode, no redis context manager is attached.
        assert getattr(g, "_redis_cm", None) is None
        # The checkpointer must be the same one the graph uses internally.
        assert isinstance(g._async_checkpointer, graph_mod.InMemorySaver)
    finally:
        await graph_mod.aclose_agent_graph(g)


@pytest.mark.asyncio
async def test_aclose_handles_missing_attributes_gracefully() -> None:
    """``aclose_agent_graph`` must not raise on graphs without the lifecycle attrs."""
    from app.agent import graph as graph_mod

    class _Naked:
        pass

    # Should be a no-op rather than raising AttributeError.
    await graph_mod.aclose_agent_graph(_Naked())
