# tests/fixtures/llm_fixtures.py

import os
from typing import Any, List

import litellm  # type: ignore[import]
import pytest
from langchain_community.utilities import WikipediaAPIWrapper
from pydantic.type_adapter import TypeAdapter

# Pydantic models from the central schema registry
from app.agent.schemas import (
    Synopsis,
    CharacterProfile,
    InitialPlan,
    CharacterArchetypeList,
    CharacterCastingDecision,
    QuestionList,
)
# API models
from app.models.api import FinalResult

# Modules to patch
from app.services import llm_service as llm_mod
from app.agent import llm_helpers as helpers_mod
from app.agent.tools import content_creation_tools as ctools
from app.agent.tools import data_tools as dtools


# -------------------------------
# Fake LLM service
# -------------------------------


class _FakeLLMService:
    """
    Test double for LLMService:

    - Returns *already-structured* objects matching what tools expect.
    - Handles both direct Pydantic models (InitialPlan, QuestionList, etc.)
      and TypeAdapter-based models (e.g. List[CharacterProfile]).
    - Ignores messages / schemas; we care only about shapes here.
    """

    async def get_structured_response(
        self,
        *,
        tool_name: str | None = None,
        messages: Any = None,
        response_model: Any = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        **_: Any,
    ):
        # ---------------------------
        # 1. TypeAdapter-based calls
        # ---------------------------
        if isinstance(response_model, TypeAdapter):
            # profile_batch_writer: List[CharacterProfile]
            if tool_name == "profile_batch_writer":
                names = ["The Optimist", "The Analyst", "The Skeptic"]
                return [
                    CharacterProfile(
                        name=n,
                        short_description=f"{n} short",
                        profile_text=f"{n} profile",
                    )
                    for n in names
                ]

            # character_list_generator fallback: List[str]
            if tool_name == "character_list_generator":
                return ["The Optimist", "The Analyst", "The Skeptic"]

            # Generic fallback for any other TypeAdapter usage:
            # validate an empty list if possible.
            try:
                return response_model.validate_python([])
            except Exception:
                return []

        # ----------------------------------
        # 2. Planning tools (by model class)
        # ----------------------------------
        if response_model is InitialPlan:
            # Used by plan_quiz / initial_planner
            return InitialPlan(
                title="Quiz: Cats",
                synopsis="A fun quiz about Cats.",
                ideal_archetypes=[
                    "The Optimist",
                    "The Analyst",
                    "The Skeptic",
                    "The Realist",
                ],
                ideal_count_hint=4,
            )

        if response_model is CharacterArchetypeList:
            # Used by generate_character_list primary path
            return CharacterArchetypeList(
                archetypes=["The Optimist", "The Analyst", "The Skeptic"]
            )

        if response_model is CharacterCastingDecision:
            # Used by select_characters_for_reuse
            return CharacterCastingDecision(
                reuse=[],
                improve=[],
                create=["The Optimist", "The Analyst"],
            )

        # ------------------------------------
        # 3. Content tools (by model class)
        # ------------------------------------
        if response_model is Synopsis:
            return Synopsis(
                title="Quiz: Cats",
                summary="A friendly quiz exploring Cats.",
            )

        if response_model is CharacterProfile:
            # Used by draft_character_profile
            return CharacterProfile(
                name="The Optimist",
                short_description="Bright outlook",
                profile_text="Always sees the good in every situation.",
            )

        if response_model is QuestionList:
            # Used by generate_baseline_questions
            # Return 3 baseline questions, each with options.
            return QuestionList(
                questions=[
                    {
                        "question_text": "Pick one",
                        "options": [{"text": "A"}, {"text": "B"}],
                    },
                    {
                        "question_text": "Choose a vibe",
                        "options": [{"text": "Cozy"}, {"text": "Noir"}],
                    },
                    {
                        "question_text": "Another?",
                        "options": [{"text": "Yes"}, {"text": "No"}],
                    },
                ]
            )

        if response_model is FinalResult:
            # Used by write_final_user_profile
            return FinalResult(
                title="You are The Optimist",
                description="Cheery and upbeat.",
                image_url=None,
            )

        # ------------------------------------------------
        # 4. Generic / Fallback by model name (internal)
        # ------------------------------------------------
        model_name = getattr(response_model, "__name__", "")

        if model_name == "QuestionOut":
            # Used by generate_next_question
            # Construct via the model itself so nested options validate.
            return response_model(
                question_text="Adaptive Q",
                options=[{"text": "One"}, {"text": "Two"}],
            )

        if model_name == "NextStepDecision":
            # Used by decide_next_step
            return response_model(
                action="ASK_ONE_MORE_QUESTION",
                confidence=0.5,
                winning_character_name=None,
            )

        # --------------------
        # 5. Last-resort stub
        # --------------------
        try:
            # Many Pydantic models support an empty constructor
            return response_model()
        except Exception:
            return None

    async def get_embedding(self, *, input: Any, **_: Any):
        """
        Simple embedding stub: returns a zero vector per input element.
        """
        dim = 1536
        out: List[List[float]] = []
        if isinstance(input, (list, tuple)):
            for _ in input:
                out.append([0.0] * dim)
        else:
            out.append([0.0] * dim)
        return out


# -------------------------------
# Fake web + wikipedia tools
# -------------------------------


class _FakeTool:
    """Mimic a LangChain tool with async `.ainvoke` returning constant text."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> str:
        return self._text


# -------------------------------
# Autouse patching
# -------------------------------


@pytest.fixture(autouse=True)
def patch_llm_everywhere(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """
    Autouse fixture:

    - Replaces the llm_service singleton in app.services with a fake.
    - Replaces llm_helpers.llm_service (the main consumer) with the same fake.
    - Neutralizes networked tools (web_search, Wikipedia) UNLESS the test
      is marked with `no_tool_stubs`.
    - Ensures OpenAI / LiteLLM don't need real credentials.
    """
    fake = _FakeLLMService()

    # 1) Swap the source of truth in the service module
    monkeypatch.setattr(llm_mod, "llm_service", fake, raising=True)

    # 2) Patch the helper, which is the gateway for all tools
    monkeypatch.setattr(helpers_mod, "llm_service", fake, raising=True)

    # 3) Patch individual tool modules that might use llm_service directly
    monkeypatch.setattr(dtools, "llm_service", fake, raising=False)
    
    # 4) Safety: ensure no real OpenAI / LiteLLM keys are required
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("USE_MEMORY_SAVER", "1")

    # 5) Conditional Stubbing of Data Tools
    # If the test is marked 'no_tool_stubs', we WANT the real tool logic to run
    # (using the fake LLM we just patched in).
    # If the marker is missing (integration tests), we stub them out completely
    # to avoid any accidental network/logic execution.
    if request.node.get_closest_marker("no_tool_stubs"):
        return

    # -- Neutralization Zone (only for non-unit tests) --
    
    # Web search is replaced by a no-op LangChain-like tool.
    monkeypatch.setattr(dtools, "web_search", _FakeTool(""), raising=False)

    # Wikipedia: patch the CLASS method so all instances are neutered
    def _fake_wiki_run(self, query: str, *args: Any, **kwargs: Any) -> str:
        return ""

    monkeypatch.setattr(WikipediaAPIWrapper, "run", _fake_wiki_run, raising=True)


@pytest.fixture
def no_rag(monkeypatch: pytest.MonkeyPatch):
    """
    Disable any character-context RAG helper if present.

    This is mostly defensive: current content_creation_tools is strictly
    zero-knowledge, but if `_fetch_character_context` ever exists again,
    this keeps unit tests deterministic.
    """

    async def _noop(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr(ctools, "_fetch_character_context", _noop, raising=False)
    return None


@pytest.fixture
def llm_spy(monkeypatch: pytest.MonkeyPatch):
    """
    Spy on the structured LLM call while delegating to the fake implementation.

    Captured fields:
      - tool_name
      - response_model (class or TypeAdapter)
      - kwargs (full)
      - call_count
    """
    # We always go through helpers_mod.llm_service in production code,
    # and patch_llm_everywhere already replaced it with _FakeLLMService.
    service = helpers_mod.llm_service
    original = service.get_structured_response

    info: dict[str, Any] = {
        "tool_name": None,
        "response_model": None,
        "kwargs": None,
        "call_count": 0,
    }

    async def wrapper(*args: Any, **kwargs: Any):
        info["call_count"] += 1
        info["tool_name"] = kwargs.get("tool_name")
        info["response_model"] = kwargs.get("response_model")
        info["kwargs"] = kwargs
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "get_structured_response", wrapper, raising=True)
    return info


@pytest.fixture(autouse=True, scope="session")
def _litellm_bg_off():
    """
    Disable LiteLLM background callback workers in tests to prevent
    'Queue ... is bound to a different event loop' issues.
    """
    os.environ["LITELLM_DISABLE_BACKGROUND_WORKER"] = "1"
    try:
        litellm.disable_background_callback_workers()  # type: ignore[attr-defined]
    except Exception:
        # Older liteLLM versions may not have this; ignore.
        pass
    yield
