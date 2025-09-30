# backend/tests/fixtures/agent_graph_fixtures.py
"""
Agent Graph test fixtures.

These fixtures and helpers target the LangGraph defined in `app.agent.graph`
and are designed for fast, deterministic tests that mirror production shapes.

What you get:
- agent_graph_memory_saver: compiles the real graph with an in-memory checkpointer
  (by temporarily setting settings.app.environment='local' and USE_MEMORY_SAVER=1).
- agent_thread_id: a fresh UUID per test for use as the thread_id.
- build_graph_config: convenience factory to attach `thread_id` (and optional db_session)
  to the runnable config (accessible from tools via config.get("configurable", {})).
- run_quiz_start: run the "start" phase (bootstrap → generate_characters → END).
- run_quiz_proceed: flip the gate and generate baseline questions on the next run.
- get_graph_state: retrieve the latest saved state for assertions (tolerates different
  LangGraph return shapes); supports both .get_state and .aget_state.
- assert_synopsis_and_characters: tiny assertion helper used in tests.
- use_fake_agent_graph: patch app startup to use a deterministic in-memory FakeAgentGraph.

Notes:
- The graph nodes are idempotent. Running the graph again with the same thread_id
  will continue from the last checkpoint.
- You can pass a SQLAlchemy AsyncSession through the runnable config to tools that
  expect it (e.g., data_tools.search_for_contextual_sessions). See `build_graph_config`.
- The fake graph intentionally mirrors production field names and shapes:
  * Character dicts DO NOT include an "id" key (keeps compatibility with Pydantic models
    configured with extra='forbid').
  * Baseline questions: { "question_text": str, "options": [{ "text": str }, ...] }.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List, TypedDict

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage

# Ensure `backend/` is importable (so `from app....` works when tests are run from repo root)
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# These imports exist in the real app; the fake graph fixture patches the create/close symbols in app.main
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
    Tiny wrapper for the "configurable" slot LangGraph/LangChain expose,
    with common knobs used by our tools.
    """
    thread_id: str
    db_session: Optional[Any] = None
    # Add more items as needed (e.g., feature flags) without breaking callers.

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
# Core graph fixtures (real graph with MemorySaver)
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
# Helpers to run phases (functions, not fixtures, so you can compose freely)
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
        assert state.get("baseline_ready") is True, "Expected baseline_ready to be True after proceed()"
        assert isinstance(qs, list), "generated_questions should be a list"
        # Spot-check expected production shape
        if qs:
            q0 = qs[0]
            assert "question_text" in q0 and isinstance(q0["question_text"], str), "question_text missing or not a string"
            opts = q0.get("options") or []
            assert isinstance(opts, list) and all(isinstance(o, dict) and "text" in o for o in opts), \
                "options should be a list[{'text': str}, ...]"
    return state


# --------------------------------------------------------------------------------------
# State access (works across LangGraph versions)
# --------------------------------------------------------------------------------------

async def get_graph_state(agent_graph, session_id: uuid.UUID) -> Dict[str, Any]:
    """
    Fetch the current values portion of the state for the given thread_id.

    LangGraph variants expose `get_state(config)` (sometimes sync) or `aget_state(config)` (async)
    that return either:
      - a `StateSnapshot`-like object with `.values` (preferred), or
      - a bare dict containing "values", or
      - already-returned values dicts.

    This helper tolerates all of the above.
    """
    cfg = build_graph_config(session_id)

    # Try async alias first if present
    try:
        aget = getattr(agent_graph, "aget_state", None)
        if callable(aget):
            snapshot = await aget(cfg)
        else:
            raise AttributeError
    except Exception:
        # Try async get_state
        try:
            snapshot = await agent_graph.get_state(cfg)  # type: ignore[attr-defined]
        except TypeError:
            # Older builds provide a sync method
            snapshot = agent_graph.get_state(cfg)  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback: some builds don’t expose get_state until after a run; re-run a no-op and inspect return
            result = await agent_graph.ainvoke({}, config=cfg)
            if isinstance(result, dict):
                return result
            raise

    # Normalize to the `.values` dict
    try:
        if isinstance(snapshot, dict) and "values" in snapshot:
            return dict(snapshot["values"])
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            return dict(values)
    except Exception:
        pass

    # As a last resort, return snapshot if it already looks like values
    if isinstance(snapshot, dict):
        return snapshot
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
    "use_fake_agent_graph",
]


# --------------------------------------------------------------------------------------
# Fake agent graph (used by API smoke tests)
# --------------------------------------------------------------------------------------

class FakeStateSnapshot:
    def __init__(self, values: dict) -> None:
        self.values = values


class _Option(TypedDict):
    text: str


class _Question(TypedDict):
    question_text: str
    options: List[_Option]


class FakeAgentGraph:
    """
    Minimal but production-shaped agent graph used by API tests.

    Behaviors:
      - `ainvoke`: merges deltas into stored state for the thread_id, and simulates the
        two main phases:
          * Phase 1 (start): ensures a non-empty `category_synopsis` and a small set of
            `generated_characters`.
          * Phase 2 (proceed): when `ready_for_questions` is True, sets `baseline_ready`
            and a stub list of `generated_questions` with production-like shape.
      - `get_state` / `aget_state`: return a snapshot-like object with `.values`.
      - `astream`: yields a couple ticks to mimic streaming, and injects characters once.

    This deliberately oversupplies fields seen in production state to make tests resilient.
    """

    def __init__(self) -> None:
        self._store: Dict[str, dict] = {}

    # --- utilities -------------------------------------------------------------

    @staticmethod
    def _thread_id_from(config: dict) -> str:
        return str(config.get("configurable", {}).get("thread_id") or "thread")

    @staticmethod
    def _ensure_defaults(s: dict) -> dict:
        s.setdefault("error_count", 0)
        s.setdefault("error_message", None)
        s.setdefault("is_error", False)
        s.setdefault("rag_context", [])
        s.setdefault("ideal_archetypes", ["The Optimist", "The Analyst", "The Skeptic"])
        s.setdefault("generated_characters", [])
        s.setdefault("generated_questions", [])
        s.setdefault("quiz_history", [])
        s.setdefault("baseline_count", 0)
        s.setdefault("baseline_ready", False)
        s.setdefault("ready_for_questions", False)
        s.setdefault("final_result", None)
        s.setdefault("last_served_index", -1)
        return s

    @staticmethod
    def _mk_characters(category: str) -> List[dict]:
        # Intentionally *no* "id" field to remain compatible with Pydantic models
        # configured with extra='forbid'.
        return [
            {
                "name": "The Optimist",
                "short_description": "Bright outlook",
                "profile_text": f"Always sees the good in {category}.",
            },
            {
                "name": "The Analyst",
                "short_description": "Thinks deeply",
                "profile_text": f"Loves data and logic about {category}.",
            },
            {
                "name": "The Traditionalist",
                "short_description": "Values the classics",
                "profile_text": f"Prefers the well-known aspects of {category}.",
            },
        ]

    @staticmethod
    def _mk_synopsis(category: str) -> dict:
        return {
            "title": f"Quiz: {category}".strip(),
            "summary": f"A friendly quiz exploring {category}.",
        }

    @staticmethod
    def _mk_baseline_questions(category: str) -> List[_Question]:
        # Matches production-y shape: {question_text, options:[{text}, ...]}
        return [
            {
                "question_text": f"Which best describes an iconic element of {category}?",
                "options": [{"text": t} for t in ["Witty banter", "Fast cars", "Deep space", "Underwater cities"]],
            },
            {
                "question_text": f"Pick the best-known setting related to {category}.",
                "options": [{"text": t} for t in ["Stars Hollow", "Gotham", "Metropolis", "The Shire"]],
            },
            {
                "question_text": f"What vibe most fits {category}?",
                "options": [{"text": t} for t in ["Cozy", "Cyberpunk", "Noir", "Post-apocalyptic"]],
            },
        ]

    # --- graph-like API --------------------------------------------------------

    async def ainvoke(self, state: dict, config: dict) -> dict:
        thread_id = self._thread_id_from(config)
        current = dict(self._store.get(thread_id, {}))
        # Merge delta (simple shallow merge is fine for tests)
        s = {**current, **(state or {})}
        s = self._ensure_defaults(s)

        category = s.get("category") or "General"

        # Phase 1 (bootstrap/start): ensure synopsis + characters
        if not s.get("category_synopsis"):
            s["category_synopsis"] = self._mk_synopsis(category)
        if not s.get("generated_characters"):
            s["generated_characters"] = self._mk_characters(category)

        # Phase 2 (proceed): baseline questions
        if s.get("ready_for_questions") and not s.get("baseline_ready"):
            qs = self._mk_baseline_questions(category)
            s["generated_questions"] = qs
            s["baseline_count"] = len(qs)
            s["baseline_ready"] = True
            # The API often serves from index 0 on the next /next call
            s.setdefault("last_served_index", -1)

        # Save and return
        self._store[thread_id] = s
        return s

    async def astream(self, state: dict, config: dict):
        """
        Simple stream: yield a couple "ticks" and ensure characters get injected once.
        Not heavily used by the HTTP API tests, but available.
        """
        thread_id = self._thread_id_from(config)
        yield {"tick": 1}
        s = dict(self._store.get(thread_id, {}))
        s = self._ensure_defaults({**s, **(state or {})})
        category = s.get("category") or "General"

        if not s.get("generated_characters"):
            s["generated_characters"] = self._mk_characters(category)
            self._store[thread_id] = s

        yield {"tick": 2}

    # IMPORTANT: the API calls get_state/aget_state in different places
    async def get_state(self, config: dict) -> FakeStateSnapshot:  # async to mirror production
        thread_id = self._thread_id_from(config)
        return FakeStateSnapshot(values=self._store.get(thread_id, {}))

    # Async alias for forward-compat
    async def aget_state(self, config: dict) -> FakeStateSnapshot:
        return await self.get_state(config)

    # Optional explicit close to mirror real graphs (not required by tests)
    async def aclose(self) -> None:  # pragma: no cover
        self._store.clear()

    def __repr__(self) -> str:  # nice for logs
        return f"<FakeAgentGraph store_threads={len(self._store)}>"

@pytest.fixture(scope="function")
def use_fake_agent_graph(monkeypatch):
    """
    Patch app startup to use FakeAgentGraph for THIS test only.

    Use via: @pytest.mark.usefixtures("use_fake_agent_graph")

    We patch the *app.main* module factory/closer, because the FastAPI lifespan uses
    those symbols to initialize and tear down the agent graph for the HTTP layer.
    """
    from tests.fixtures.agent_graph_fixtures import FakeAgentGraph
    import app.main as main_mod
    import app.agent.graph as graph_mod

    async def _create():
        return FakeAgentGraph()

    async def _close(_graph):
        try:
            await _graph.aclose()  # tolerant
        except Exception:
            pass

    # Patch both the indirection on main *and* the direct module used by lifespan
    monkeypatch.setattr(main_mod, "create_agent_graph", _create, raising=True)
    monkeypatch.setattr(main_mod, "aclose_agent_graph", _close, raising=True)
    monkeypatch.setattr(graph_mod, "create_agent_graph", _create, raising=True)
    monkeypatch.setattr(graph_mod, "aclose_agent_graph", _close, raising=True)

    yield

