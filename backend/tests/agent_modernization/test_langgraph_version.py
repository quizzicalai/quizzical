"""Modernization gate: pin to LangGraph 1.x."""

from __future__ import annotations

from importlib import metadata


def test_langgraph_is_v1_or_newer() -> None:
    version = metadata.version("langgraph")
    major = int(version.split(".", 1)[0])
    assert major >= 1, f"Expected LangGraph >= 1.0, got {version}"


def test_inmemorysaver_is_canonical_import() -> None:
    """LangGraph 1.x exports InMemorySaver as the canonical in-memory checkpointer."""
    from langgraph.checkpoint.memory import InMemorySaver  # noqa: F401


def test_graph_module_imports_cleanly_on_langgraph_1x() -> None:
    """The agent graph module must continue to import without errors."""
    import importlib

    module = importlib.import_module("app.agent.graph")
    assert hasattr(module, "create_agent_graph")
    assert hasattr(module, "aclose_agent_graph")
    assert hasattr(module, "workflow")
