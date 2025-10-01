# tests/fixtures/tool_fixtures.py
import os
import types
import uuid
import pytest

# Graph (needs patching for names it bound at import time)
import app.agent.graph as graph_mod

# Tool modules (patch at source so any callers elsewhere are covered too)
from app.agent.tools import (
    planning_tools as ptools,
    content_creation_tools as ctools,
    analysis_tools as atools,
    data_tools as dtools,
    image_tools as itools,
    utility_tools as utools,
)

# Models used across tools
from app.agent.state import Synopsis, CharacterProfile
from app.agent.schemas import (
    InitialPlan,
    NormalizedTopic,
    CharacterArchetypeList,
    CharacterCastingDecision,
    NextStepDecision,
    QuestionOut,
    QuestionList,
)
from app.models.api import FinalResult


def _live_tools_enabled(request) -> bool:
    # Prefer CLI flag if present; also allow env var fallback
    return bool(
        getattr(getattr(request, "config", None), "option", None)
        and getattr(request.config.option, "live_tools", False)
        or os.getenv("LIVE_TOOLS", "").lower() in {"1", "true", "yes"}
    )


# -----------------------------
# Shared tiny helpers
# -----------------------------

def _as_tool(obj):
    """
    Wrap a simple value or callable into a LangChain-like tool stub with
    both .ainvoke (async) and .invoke (sync) entry points.
    """
    class _T:
        def __init__(self, value):
            self._value = value

        async def ainvoke(self, payload=None, **_):
            if callable(self._value):
                return await self._value(payload) if _is_coro(self._value) else self._value(payload)
            return self._value

        def invoke(self, payload=None, **_):
            if callable(self._value):
                return self._value(payload)
            return self._value

    return _T(obj)


def _is_coro(fn):
    try:
        import inspect
        return inspect.iscoroutinefunction(fn)
    except Exception:
        return False


# ======================================================================================
# AUTOUSE: Stub every tool by default (fast & deterministic). Opt-out with --live-tools.
# ======================================================================================
@pytest.fixture(autouse=True)
def stub_all_tools(monkeypatch, request):
    if _live_tools_enabled(request):
        # Use real tools when explicitly requested
        return

    # ============================================================
    # PLANNING TOOLS
    # ============================================================

    class StubNormalizeTopic:
        async def ainvoke(self, payload):
            raw = (payload or {}).get("category") or "General"
            return NormalizedTopic(
                category=str(raw).strip() or "General",
                outcome_kind="archetypes",
                creativity_mode="balanced",
                rationale="stub",
            )
        def invoke(self, payload):  # sync compatibility
            return types.SimpleNamespace(**(self._to_dict(payload)))

    class StubPlanQuiz:
        async def ainvoke(self, payload):
            cat = (payload or {}).get("category") or "General"
            return InitialPlan(
                synopsis=f"A quick quiz about {cat}.",
                ideal_archetypes=["Explorer", "Analyst", "Dreamer", "Realist"],
            )
        def invoke(self, payload):  # sync compatibility
            return InitialPlan(synopsis="...", ideal_archetypes=["Explorer", "Analyst"])

    class StubCharacterList:
        async def ainvoke(self, payload):
            seeds = (payload or {}).get("seed_archetypes") or []
            return CharacterArchetypeList(archetypes=(seeds or ["Explorer", "Analyst", "Dreamer"]))
        def invoke(self, payload):
            return ["Explorer", "Analyst", "Dreamer"]

    class StubSelectCharacters:
        async def ainvoke(self, payload):
            return CharacterCastingDecision(reuse=[], improve=[], create=(payload or {}).get("ideal_archetypes", []))
        def invoke(self, payload):
            return {"reuse": [], "improve": [], "create": (payload or {}).get("ideal_archetypes", [])}

    # Patch planning tools at source
    monkeypatch.setattr(ptools, "normalize_topic", StubNormalizeTopic(), raising=True)
    monkeypatch.setattr(ptools, "plan_quiz", StubPlanQuiz(), raising=True)
    monkeypatch.setattr(ptools, "generate_character_list", StubCharacterList(), raising=True)
    monkeypatch.setattr(ptools, "select_characters_for_reuse", StubSelectCharacters(), raising=True)

    # Also patch names bound in graph
    monkeypatch.setattr(graph_mod, "tool_normalize_topic", ptools.normalize_topic, raising=True)
    monkeypatch.setattr(graph_mod, "tool_plan_quiz", ptools.plan_quiz, raising=True)
    monkeypatch.setattr(graph_mod, "tool_generate_character_list", ptools.generate_character_list, raising=True)

    # ============================================================
    # CONTENT CREATION TOOLS
    # ============================================================

    class StubSynopsis:
        async def ainvoke(self, payload):
            cat = (payload or {}).get("category") or "General"
            return Synopsis(title=f"Quiz: {cat}", summary=f"A friendly quiz about {cat}.")
        def invoke(self, payload):
            cat = (payload or {}).get("category") or "General"
            return {"title": f"Quiz: {cat}", "summary": f"A friendly quiz about {cat}."}

    class StubDraftCharacter:
        async def ainvoke(self, payload):
            name = (payload or {}).get("character_name") or "Character"
            return CharacterProfile(
                name=name,
                short_description=f"{name} short",
                profile_text=f"{name} profile",
            )
        def invoke(self, payload):
            return {"name": "Character", "short_description": "short", "profile_text": "profile"}

    class StubImproveCharacter:
        async def ainvoke(self, payload):
            prof = (payload or {}).get("existing_profile") or {}
            name = prof.get("name") or "Character"
            return CharacterProfile(
                name=name,
                short_description=(prof.get("short_description") or "improved"),
                profile_text=(prof.get("profile_text") or "improved"),
                image_url=prof.get("image_url"),
            )
        def invoke(self, payload):
            return {"name": "Character", "short_description": "improved", "profile_text": "improved"}

    class StubBaseline:
        async def ainvoke(self, payload):
            # Produce 3 questions in state-ish shape
            return [
                {"question_text": "Pick one", "options": [{"text": "A"}, {"text": "B"}]},
                {"text": "Cats or Dogs?", "options": [{"label": "Cats"}, {"label": "Dogs"}]},
                {"text": "Coffee?", "options": ["Yes", "No"]},
            ]
        def invoke(self, payload):
            return [{"question_text": "Stub Q", "options": [{"text": "Yes"}, {"text": "No"}]}]

    class StubNextQuestion:
        async def ainvoke(self, payload):
            return {"question_text": "One more?", "options": [{"text": "Yes"}, {"text": "No"}]}
        def invoke(self, payload):
            return {"question_text": "One more?", "options": [{"text": "Yes"}, {"text": "No"}]}

    class StubDecideNext:
        async def ainvoke(self, payload):
            hist = (payload or {}).get("quiz_history") or []
            # Finish if very long, otherwise ask one more
            if len(hist) >= 20:
                return NextStepDecision(action="FINISH_NOW", confidence=1.0, winning_character_name=None)
            return NextStepDecision(action="ASK_ONE_MORE_QUESTION", confidence=0.3, winning_character_name=None)
        def invoke(self, payload):
            return {"action": "ASK_ONE_MORE_QUESTION", "confidence": 0.3}

    class StubWriteFinal:
        async def ainvoke(self, payload):
            w = (payload or {}).get("winning_character") or {"name": "Winner"}
            name = (w.get("name") if isinstance(w, dict) else getattr(w, "name", None)) or "Winner"
            return FinalResult(title=f"You are {name}", description="Stubbed final profile.", image_url=None)
        def invoke(self, payload):
            return {"title": "You are Winner", "description": "Stubbed", "image_url": None}

    # Patch content tools at source
    monkeypatch.setattr(ctools, "generate_category_synopsis", StubSynopsis(), raising=True)
    monkeypatch.setattr(ctools, "draft_character_profile", StubDraftCharacter(), raising=True)
    monkeypatch.setattr(ctools, "improve_character_profile", StubImproveCharacter(), raising=True)
    monkeypatch.setattr(ctools, "generate_baseline_questions", StubBaseline(), raising=True)
    monkeypatch.setattr(ctools, "generate_next_question", StubNextQuestion(), raising=True)
    monkeypatch.setattr(ctools, "decide_next_step", StubDecideNext(), raising=True)
    monkeypatch.setattr(ctools, "write_final_user_profile", StubWriteFinal(), raising=True)

    # Also patch names bound in graph
    monkeypatch.setattr(graph_mod, "tool_generate_category_synopsis", ctools.generate_category_synopsis, raising=True)
    monkeypatch.setattr(graph_mod, "tool_draft_character_profile", ctools.draft_character_profile, raising=True)
    monkeypatch.setattr(graph_mod, "tool_generate_baseline_questions", ctools.generate_baseline_questions, raising=True)
    monkeypatch.setattr(graph_mod, "tool_generate_next_question", ctools.generate_next_question, raising=True)
    monkeypatch.setattr(graph_mod, "tool_decide_next_step", ctools.decide_next_step, raising=True)
    monkeypatch.setattr(graph_mod, "tool_write_final_user_profile", ctools.write_final_user_profile, raising=True)

    # ============================================================
    # ANALYSIS / SAFETY TOOLS
    # ============================================================

    class StubAssessSafety:
        async def ainvoke(self, payload):
            return "safe"
        def invoke(self, payload):
            return "safe"

    class StubAnalyzeError:
        async def ainvoke(self, payload):
            msg = (payload or {}).get("error_message") or ""
            return f"Retry with simpler parameters. ({msg[:60]})"
        def invoke(self, payload):
            return "Retry with simpler parameters."

    class StubExplainFailure:
        async def ainvoke(self, payload):
            return "Sorry, something went wrong. Please try again."
        def invoke(self, payload):
            return "Sorry, something went wrong. Please try again."

    monkeypatch.setattr(atools, "assess_category_safety", StubAssessSafety(), raising=True)
    monkeypatch.setattr(atools, "analyze_tool_error", StubAnalyzeError(), raising=True)
    monkeypatch.setattr(atools, "explain_failure_to_user", StubExplainFailure(), raising=True)

    # ============================================================
    # DATA / RAG TOOLS
    # ============================================================

    class StubSearchSessions:
        async def ainvoke(self, payload):
            # Return 0–2 fake hits, tolerant to any payload shape
            return [
                {
                    "session_id": str(uuid.uuid4()),
                    "category": "Cats",
                    "category_synopsis": "Cats: friendly quiz.",
                    "final_result": {"title": "You are Explorer", "description": "stub"},
                    "judge_feedback": None,
                    "user_feedback": None,
                    "distance": 0.12,
                }
            ]
        def invoke(self, payload):
            return []

    class StubFetchCharacter:
        async def ainvoke(self, payload):
            char_id = ((payload or {}).get("tool_input") or {}).get("character_id") or str(uuid.uuid4())
            return {"id": char_id, "name": "Explorer", "short_description": "stub", "profile_text": "stub"}
        def invoke(self, payload):
            return None

    class StubWiki:
        async def ainvoke(self, payload):
            return ""
        def invoke(self, payload):
            return ""

    class StubWebSearch:
        async def ainvoke(self, payload):
            return ""
        def invoke(self, payload):
            return ""

    monkeypatch.setattr(dtools, "search_for_contextual_sessions", StubSearchSessions(), raising=True)
    monkeypatch.setattr(dtools, "fetch_character_details", StubFetchCharacter(), raising=True)
    monkeypatch.setattr(dtools, "wikipedia_search", StubWiki(), raising=True)
    monkeypatch.setattr(dtools, "web_search", StubWebSearch(), raising=True)

    # ============================================================
    # IMAGE TOOLS
    # ============================================================

    class StubPromptEnhancer:
        async def ainvoke(self, payload):
            concept = (payload or {}).get("concept") or "concept"
            style = (payload or {}).get("style") or "clipart"
            return f"{concept}, {style}, high quality, clean background"
        def invoke(self, payload):
            return "prompt"

    class StubImageGen:
        async def ainvoke(self, payload):
            return "https://placehold.co/600x400/EEE/31343C?text=Stub+Image"
        def invoke(self, payload):
            return "https://placehold.co/600x400/EEE/31343C?text=Stub+Image"

    monkeypatch.setattr(itools, "create_image_generation_prompt", StubPromptEnhancer(), raising=True)
    monkeypatch.setattr(itools, "generate_image", StubImageGen(), raising=True)

    # ============================================================
    # UTILITY / PERSISTENCE TOOLS
    # ============================================================

    class StubPersistSession:
        async def ainvoke(self, payload):
            return "Session saved (stub)."
        def invoke(self, payload):
            return "Session saved (stub)."

    monkeypatch.setattr(utools, "persist_session_to_database", StubPersistSession(), raising=True)
