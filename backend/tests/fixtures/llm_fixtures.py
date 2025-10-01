# tests/fixtures/llm_fixtures.py

import types
import asyncio
import uuid
import pytest
import inspect
import os
import litellm  # type: ignore[import]

# Pydantic models used by tools
from app.agent.state import Synopsis, CharacterProfile, QuizQuestion
from app.agent.tools.planning_tools import InitialPlan, NormalizedTopic
from app.agent.tools.content_creation_tools import QuestionList
from app.models.api import FinalResult
from app.agent.tools import content_creation_tools as ctools
from app.agent.tools import planning_tools as ptools
from app.agent.tools import data_tools as dtools
from app.services import llm_service as llm_mod
import app.agent.graph as graph_mod

from langchain_community.utilities import WikipediaAPIWrapper

# -------------------------------
# Fake LLM service
# -------------------------------
class _FakeLLMService:
    async def get_structured_response(self, *, tool_name=None, messages=None,
                                      response_model=None, trace_id=None, session_id=None, **_):
        # Return a minimal valid instance for the requested model
        if response_model is Synopsis:
            return Synopsis(title="Quiz: Cats", summary="A friendly quiz exploring Cats.")
        if response_model is CharacterProfile:
            return CharacterProfile(name="The Optimist", short_description="Bright outlook", profile_text="Always sees the good.")
        if response_model is InitialPlan:
            return InitialPlan(synopsis="A fun quiz.", ideal_archetypes=["The Optimist","The Analyst","The Skeptic","The Realist"])
        if response_model is NormalizedTopic:
            return NormalizedTopic(
                category="Cats",
                outcome_kind="archetypes",
                creativity_mode="balanced",
                rationale="test"
            )
        if response_model is QuestionList:
            # Return 3 baseline questions, each with two options
            return QuestionList(questions=[
                {"question_text": "Pick one", "options": [{"text":"A"},{"text":"B"}]},
                {"question_text": "Choose a vibe", "options": [{"text":"Cozy"},{"text":"Noir"}]},
                {"question_text": "Another?", "options": [{"text":"Yes"},{"text":"No"}]},
            ])
        # The content tools also request QuestionOut (for adaptive) and NextStepDecision
        # Handle by name to avoid importing more types
        if getattr(response_model, "__name__", "") == "QuestionOut":
            return response_model(question_text="Adaptive Q", options=[{"text":"One"},{"text":"Two"}])
        if getattr(response_model, "__name__", "") == "NextStepDecision":
            # Keep asking (avoids finishing early in tests unless you want to)
            return response_model(action="ASK_ONE_MORE_QUESTION", confidence=0.5, winning_character_name=None)

        if response_model is FinalResult:
            return FinalResult(title="You are The Optimist", description="Cheery and upbeat.", image_url=None)

        # Fallback: try to construct with empty/obvious values
        try:
            return response_model()
        except Exception:
            return None

    async def get_embedding(self, *, input, **_):
        # Return a single zero vector per input item
        dim = 1536
        out = []
        if isinstance(input, (list, tuple)):
            for _ in input:
                out.append([0.0]*dim)
        else:
            out.append([0.0]*dim)
        return out

# -------------------------------
# Fake web + wikipedia tools
# -------------------------------
class _FakeTool:
    """Mimic a LangChain tool with async ainvoke returning constant text."""
    def __init__(self, text=""):
        self._text = text
    async def ainvoke(self, *_args, **_kwargs):
        return self._text

@pytest.fixture(autouse=True)
def patch_llm_everywhere(monkeypatch):
    """
    Autouse fixture:
      - Replaces the llm_service singleton
      - Also replaces each module's imported llm_service symbol
      - Neutralizes networked tools (web_search, Wikipedia)
    """
    fake = _FakeLLMService()

    # 1) Swap the source of truth
    monkeypatch.setattr(llm_mod, "llm_service", fake, raising=True)

    # 2) IMPORTANT: also replace the *imported* references inside each tools module
    monkeypatch.setattr(ctools, "llm_service", fake, raising=True)
    monkeypatch.setattr(ptools, "llm_service", fake, raising=True)
    # (data_tools only uses llm_service for embeddings; cover it too)
    monkeypatch.setattr(dtools, "llm_service", fake, raising=False)

    # 3) Kill web/wikipedia calls regardless of settings
    monkeypatch.setattr(dtools, "web_search", _FakeTool(""), raising=False)
    # Wikipedia: patch the CLASS method so all instances are neutered (avoids Pydantic v2 instance setattr issues)
    def _fake_wiki_run(self, query: str, *args, **kwargs) -> str:
        return ""
    monkeypatch.setattr(WikipediaAPIWrapper, "run", _fake_wiki_run, raising=True)
    
    monkeypatch.setattr(graph_mod, "llm_service", fake, raising=True)

    # 4) Safety: ensure no OpenAI SDK gets pulled by accident
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    # Also force MemorySaver path in the graph checkpointer
    monkeypatch.setenv("USE_MEMORY_SAVER", "1")

@pytest.fixture
def no_rag(monkeypatch):
    async def _noop(*_a, **_k): return ""
    monkeypatch.setattr(ctools, "_fetch_character_context", _noop, raising=True)
    return None

# Function names we can accept for the "structured" LLM call.
_POSSIBLE_NAMES = [
    "get_structured_response",          # preferred / legacy
    "get_structured_output",            # alternative
    "get_structured_json",              # alternative
    "get_structured_response_async",    # async variant
]

def _resolve_llm_callable(target):
    """
    Find the first present structured-response function on the target.
    Returns (attr_name, callable). Raises AttributeError if none found.
    """
    for name in _POSSIBLE_NAMES:
        if hasattr(target, name):
            fn = getattr(target, name)
            if callable(fn):
                return name, fn
    raise AttributeError(
        "content_creation_tools.llm_service has no recognized structured-response "
        f"function; tried: {', '.join(_POSSIBLE_NAMES)}"
    )

@pytest.fixture(autouse=True)
def _ensure_llm_api_compat(monkeypatch):
    """
    Ensure that content_creation_tools.llm_service always exposes a
    'get_structured_response' attribute so tests and monkeypatches that
    reference that name keep working, regardless of the underlying impl name.
    """
    target = ctools.llm_service
    try:
        # If already present, nothing to do.
        getattr(target, "get_structured_response")
        return
    except AttributeError:
        # Alias the first available implementation to the expected name.
        name, fn = _resolve_llm_callable(target)
        if name != "get_structured_response":
            monkeypatch.setattr(target, "get_structured_response", fn, raising=False)

@pytest.fixture
def llm_spy(monkeypatch):
    """
    Spy on the structured LLM call while delegating to the real implementation.

    Captured fields:
      - tool_name
      - response_model (class)
      - kwargs (full)
      - call_count
    """
    target = ctools.llm_service
    # Resolve (and potentially alias) the callable we’ll wrap.
    name, original = _resolve_llm_callable(target)

    info = {
        "tool_name": None,
        "response_model": None,
        "kwargs": None,
        "call_count": 0,
    }

    # Heuristic to pull tool name from args/kwargs in a robust way.
    def _extract_tool_name(args, kwargs):
        for k in ("tool_name", "tool", "name"):
            if k in kwargs and isinstance(kwargs[k], str):
                return kwargs[k]
        # If first arg looks like a tool name, use it.
        if args and isinstance(args[0], str):
            return args[0]
        return None

    if inspect.iscoroutinefunction(original):
        async def wrapper(*args, **kwargs):
            info["call_count"] += 1
            info["tool_name"] = _extract_tool_name(args, kwargs)
            info["response_model"] = kwargs.get("response_model")
            info["kwargs"] = kwargs
            return await original(*args, **kwargs)
    else:
        def wrapper(*args, **kwargs):
            info["call_count"] += 1
            info["tool_name"] = _extract_tool_name(args, kwargs)
            info["response_model"] = kwargs.get("response_model")
            info["kwargs"] = kwargs
            return original(*args, **kwargs)

    # Patch whichever function ctools.llm_service actually provides.
    monkeypatch.setattr(target, name, wrapper, raising=False)

    # Also expose the wrapper under the canonical name so tests that
    # monkeypatch "get_structured_response" keep working.
    if name != "get_structured_response":
        monkeypatch.setattr(target, "get_structured_response", wrapper, raising=False)

    return info

@pytest.fixture
def patch_llm_everywhere(monkeypatch):
    """
    Optional convenience fixture: force a deterministic structured response.

    Not required by the current failing test, but kept for compatibility
    with other tests that might rely on it.
    Usage:
        patch_llm_everywhere(payload_or_factory)
    Where:
        - payload_or_factory can be:
          * a concrete pydantic model instance to be returned, or
          * a callable(*args, **kwargs) -> model
    """
    target = ctools.llm_service
    name, original = _resolve_llm_callable(target)

    def _apply(fake):
        if inspect.iscoroutinefunction(original):
            if inspect.iscoroutinefunction(fake):
                async def wrapper(*args, **kwargs):
                    return await fake(*args, **kwargs)
            else:
                async def wrapper(*args, **kwargs):
                    return fake(*args, **kwargs)
        else:
            if inspect.iscoroutinefunction(fake):
                async def wrapper(*args, **kwargs):
                    return await fake(*args, **kwargs)
            else:
                def wrapper(*args, **kwargs):
                    return fake(*args, **kwargs)

        monkeypatch.setattr(target, name, wrapper, raising=False)
        # keep alias consistent
        monkeypatch.setattr(target, "get_structured_response", wrapper, raising=False)

    return _apply

@pytest.fixture(autouse=True, scope="session")
def _litellm_bg_off():
    """
    Disable LiteLLM background callback workers in tests to prevent
    'Queue ... is bound to a different event loop' issues.
    """
    os.environ["LITELLM_DISABLE_BACKGROUND_WORKER"] = "1"
    try:  # idempotent; safe on older/newer LiteLLM builds
        litellm.disable_background_callback_workers()  # type: ignore[attr-defined]
    except Exception:
        pass
    yield