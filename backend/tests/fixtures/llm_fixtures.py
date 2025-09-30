# tests/fixtures/llm_fixtures.py

import types
import asyncio
import uuid
import pytest

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
