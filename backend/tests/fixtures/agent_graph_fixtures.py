"""
Agent Graph test fixtures.

These fixtures and helpers target the LangGraph defined in
`app.agent.graph` and are designed for fast, deterministic tests.

What you get:
- agent_graph_memory_saver: compiles the graph with an in-memory checkpointer
  (by temporarily setting settings.app.environment='local' and USE_MEMORY_SAVER=1).
- agent_thread_id: a fresh UUID per test for use as the thread_id.
- build_graph_config: convenience factory to attach `thread_id` (and optional db_session)
  to the runnable config (accessible from tools via config.get("configurable", {})).
- run_quiz_start: run the "start" phase (bootstrap → generate_characters → END).
- run_quiz_proceed: flip the gate and generate baseline questions on the next run.
- get_graph_state: retrieve the latest saved state for assertions.

Notes:
- The graph nodes are idempotent. Running the graph again with the same thread_id
  will continue from the last checkpoint.
- You can pass a SQLAlchemy AsyncSession through the runnable config to tools that
  expect it (e.g., data_tools.search_for_contextual_sessions). See `build_graph_config`.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage

# Ensure `backend/` is importable (so `from app....` works when tests are run from repo root)
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.agent.graph import (  # type: ignore
    create_agent_graph,
    aclose_agent_graph,
)
from app.agent.state import GraphState  # type: ignore
from app.core.config import settings  # type: ignore


# --------------------------------------------------------------------------------------
# Public config builder
# --------------------------------------------------------------------------------------

@dataclass
class GraphConfig:
    """
    Tiny wrapper for the "configurable" slot LangGraph exposes, with common knobs used by our tools.
    """
    thread_id: str
    db_session: Optional[Any] = None
    # You can add more items as needed (e.g., feature flags) without breaking callers.

    def to_runnable_config(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"thread_id": self.thread_id}
        if self.db_session is not None:
            payload["db_session"] = self.db_session
        return {"configurable": payload}


def build_graph_config(
    session_uuid: uuid.UUID,
    *,
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Create a LangGraph runnable config that carries a stable thread_id (required for checkpointing)
    and optional kb/DB handles that tools can read via `config.get("configurable", {})`.
    """
    cfg = GraphConfig(thread_id=str(session_uuid), db_session=db_session)
    return cfg.to_runnable_config()


# --------------------------------------------------------------------------------------
# Core graph fixtures
# --------------------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def agent_graph_memory_saver(monkeypatch):
    """
    Compile the agent graph with an in-memory checkpointer (MemorySaver).

    Implementation details:
    - The graph factory prefers Redis unless `env in {"local","dev","development"}`
      AND `USE_MEMORY_SAVER` is truthy. We transiently set both so tests don't
      require Redis.
    - We ensure cleanup by calling the graph's provided async close helper.
    """
    # Force the code path that selects MemorySaver in create_agent_graph()
    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    # Some settings objects can be immutable; setattr(..., raising=False) keeps this tolerant.
    try:
        monkeypatch.setattr(settings.app, "environment", "local", raising=False)  # type: ignore[attr-defined]
    except Exception:
        # If settings.app doesn't exist (unlikely in your app), just set an env the function might consult later.
        monkeypatch.setenv("APP_ENV", "local")

    graph = await create_agent_graph()
    try:
        yield graph
    finally:
        # Ensure any async checkpointer contexts are closed to avoid resource leaks across tests
        try:
            await aclose_agent_graph(graph)
        except Exception:
            pass


@pytest.fixture(scope="function")
def agent_thread_id() -> uuid.UUID:
    """Provide a fresh, stable UUID per test for use as the LangGraph thread_id."""
    return uuid.uuid4()


# --------------------------------------------------------------------------------------
# Helpers to run phases (these are functions, not fixtures, so you can compose freely)
# --------------------------------------------------------------------------------------

async def run_quiz_start(
    agent_graph,
    *,
    session_id: uuid.UUID,
    category: str = "Cats",
    trace_id: str = "test-trace",
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Run the initial quiz phase.
    This executes: bootstrap → generate_characters → router(END) since the gate is off.

    Returns the latest state dict (values only) so tests can assert on synopsis, characters, etc.
    """
    initial_state: GraphState = {
        "session_id": session_id,
        "trace_id": trace_id,
        "category": category,
        # The graph tolerates missing messages, but including the first user message matches production shape.
        "messages": [HumanMessage(content=category)],
        "ready_for_questions": False,
    }
    config = build_graph_config(session_id, db_session=db_session)
    # First pass: seed and run prep
    await agent_graph.ainvoke(initial_state, config=config)
    # Return the saved state snapshot for the given thread
    return await get_graph_state(agent_graph, session_id)


async def run_quiz_proceed(
    agent_graph,
    *,
    session_id: uuid.UUID,
    trace_id: str = "test-trace",
    db_session: Optional[Any] = None,
    # Optionally override whether we expect baseline questions to be produced on this pass
    expect_baseline: bool = True,
) -> Dict[str, Any]:
    """
    Flip the `ready_for_questions` gate and run the graph again.
    On this pass the router will direct to generate_baseline_questions (then assemble_and_finish).
    """
    delta = {
        "ready_for_questions": True,
        "trace_id": trace_id,
    }
    config = build_graph_config(session_id, db_session=db_session)
    await agent_graph.ainvoke(delta, config=config)
    state = await get_graph_state(agent_graph, session_id)

    # Optional sanity checks to catch regressions early (do not raise unless requested)
    if expect_baseline:
        qs = state.get("generated_questions") or []
        # It's valid for zero baseline questions to be returned, but baseline_ready should be True if the node ran.
        assert state.get("baseline_ready") is True, "Expected baseline_ready to be True after proceed()"
        assert isinstance(qs, list), "generated_questions should be a list"
    return state


# --------------------------------------------------------------------------------------
# State access (works across LangGraph versions)
# --------------------------------------------------------------------------------------

async def get_graph_state(agent_graph, session_id: uuid.UUID) -> Dict[str, Any]:
    """
    Fetch the current values portion of the state for the given thread_id.

    LangGraph variants expose `get_state(config)` as sync or async and return either:
      - a `StateSnapshot`-like object with `.values` (preferred), or
      - a bare dict containing "values".

    This helper tolerates both.
    """
    cfg = build_graph_config(session_id)
    # Try async first
    try:
        snapshot = await agent_graph.get_state(cfg)  # type: ignore[attr-defined]
    except TypeError:
        # Older builds provide a sync method
        snapshot = agent_graph.get_state(cfg)  # type: ignore[attr-defined]
    except AttributeError:
        # Fallback: some builds hang state on a different attribute; as a last resort, re-run a no-op and inspect return
        result = await agent_graph.ainvoke({}, config=cfg)
        if isinstance(result, dict):
            # Many builds return values directly on ainvoke
            return result
        raise

    # Normalize to the `.values` dict
    try:
        if isinstance(snapshot, dict) and "values" in snapshot:
            return dict(snapshot["values"])
        # StateSnapshot-like
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            return dict(values)
    except Exception:
        pass

    # As a last resort, return snapshot if it already looks like values
    if isinstance(snapshot, dict):
        return snapshot
    # Unknown shape → best effort empty dict
    return {}


# --------------------------------------------------------------------------------------
# Example assertion helper (optional; handy in tests)
# --------------------------------------------------------------------------------------

def assert_synopsis_and_characters(state: Dict[str, Any], min_chars: int = 1) -> None:
    """
    Quick sanity assertion commonly used in tests that validate phase 1.
    """
    syn = state.get("category_synopsis")
    chars = state.get("generated_characters") or []
    assert syn is not None, "Expected a synopsis in state after run_quiz_start()"
    assert isinstance(chars, list) and len(chars) >= min_chars, f"Expected >= {min_chars} character profiles"


__all__ = [
    # fixtures
    "agent_graph_memory_saver",
    "agent_thread_id",
    # builders/helpers
    "build_graph_config",
    "run_quiz_start",
    "run_quiz_proceed",
    "get_graph_state",
    "assert_synopsis_and_characters",
]
