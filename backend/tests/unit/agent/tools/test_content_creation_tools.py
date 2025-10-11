# tests/unit/tools/test_content_creation_tools.py

import pytest
import types

from app.agent.tools import content_creation_tools as ctools
from app.agent.tools.content_creation_tools import (
    generate_category_synopsis as _real_generate_category_synopsis,
    draft_character_profile as _real_draft_character_profile,
    generate_baseline_questions as _real_generate_baseline_questions,
    generate_next_question as _real_generate_next_question,
    decide_next_step as _real_decide_next_step,
    write_final_user_profile as _real_write_final_user_profile,
    improve_character_profile as _real_improve_character_profile,
)
from app.agent.state import Synopsis, CharacterProfile, QuizQuestion
from app.agent.schemas import (
    NextStepDecision,
    QuestionList,
    QuestionOut,
    QuestionOption,
)
from tests.helpers.builders import make_question_list_with_dupes
from tests.helpers.samples import sample_character, sample_synopsis


pytestmark = pytest.mark.unit


# Ensure autouse tool stubs are bypassed for this module: we want real implementations.
@pytest.fixture(autouse=True)
def _restore_real_content_tools(monkeypatch):
    monkeypatch.setattr(ctools, "generate_category_synopsis", _real_generate_category_synopsis, raising=False)
    monkeypatch.setattr(ctools, "draft_character_profile", _real_draft_character_profile, raising=False)
    monkeypatch.setattr(ctools, "generate_baseline_questions", _real_generate_baseline_questions, raising=False)
    monkeypatch.setattr(ctools, "generate_next_question", _real_generate_next_question, raising=False)
    monkeypatch.setattr(ctools, "decide_next_step", _real_decide_next_step, raising=False)
    monkeypatch.setattr(ctools, "write_final_user_profile", _real_write_final_user_profile, raising=False)
    # Also restore improve_character_profile so we can test its real behavior
    monkeypatch.setattr(ctools, "improve_character_profile", _real_improve_character_profile, raising=False)


# ---------------------------------------------------------------------------
# generate_category_synopsis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_category_synopsis_calls_llm_and_returns_text(ids, llm_spy):
    out = await ctools.generate_category_synopsis.ainvoke(
        {"category": "Cats", **ids}
    )
    assert isinstance(out, Synopsis)
    assert out.title.startswith("Quiz:")
    assert llm_spy["tool_name"] == "synopsis_generator"
    assert getattr(llm_spy["response_model"], "__name__", "") == "Synopsis"


@pytest.mark.asyncio
async def test_generate_category_synopsis_fallback_on_llm_error(ids, monkeypatch):
    async def _boom(**_):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(
        ctools.llm_service, "get_structured_response", _boom, raising=True
    )

    out = await ctools.generate_category_synopsis.ainvoke(
        {"category": "Cats", **ids}
    )
    # Fallback uses normalized title and empty summary
    assert isinstance(out, Synopsis)
    assert out.title.startswith("Quiz:")
    assert out.summary == ""


@pytest.mark.asyncio
async def test_generate_category_synopsis_normalizes_quiz_prefix(ids, monkeypatch):
    async def _fake_gsr(**kwargs):
        return Synopsis(title="quiz - cats", summary="about cats")
    monkeypatch.setattr(ctools.llm_service, "get_structured_response", _fake_gsr, raising=True)

    out = await ctools.generate_category_synopsis.ainvoke({"category": "cats", **ids})
    assert out.title == "Quiz: cats"  # prefix normalized and lower preserved


# ---------------------------------------------------------------------------
# draft_character_profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_draft_character_profile_works_with_no_rag_fixture(ids, no_rag):
    out = await ctools.draft_character_profile.ainvoke(
        {"character_name": "Lorelai Gilmore", "category": "Gilmore Girls", **ids}
    )
    assert isinstance(out, CharacterProfile)
    assert out.name  # preserved/filled


@pytest.mark.asyncio
async def test_draft_character_profile_calls_rag_for_media_only(ids, monkeypatch):
    calls = {"count": 0}

    async def _counting_fetch(character_name, normalized_category, trace_id, session_id):
        calls["count"] += 1
        return "context text"

    # If the helper exists, this will intercept the RAG fetch path.
    monkeypatch.setattr(
        ctools, "_fetch_character_context", _counting_fetch, raising=True
    )

    # Media-like category -> should fetch context
    media = await ctools.draft_character_profile.ainvoke(
        {"character_name": "Lorelai Gilmore", "category": "Gilmore Girls", **ids}
    )
    assert isinstance(media, CharacterProfile)
    assert calls["count"] >= 1

    calls["count"] = 0

    # Non-media types category -> should not fetch
    non_media = await ctools.draft_character_profile.ainvoke(
        {"character_name": "The Optimist", "category": "Types of Salad", **ids}
    )
    assert isinstance(non_media, CharacterProfile)
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_draft_character_profile_uses_profile_writer_tool(ids, llm_spy):
    await ctools.draft_character_profile.ainvoke(
        {"character_name": "Luke", "category": "Gilmore Girls", **ids}
    )
    assert llm_spy["tool_name"] == "profile_writer"  # canonical or generic path both use this


@pytest.mark.asyncio
async def test_draft_character_profile_canonical_branch_fallback_preserves_name(ids, monkeypatch):
    # Force the canonical branch to throw, then ensure fallback keeps the requested name.
    async def _boom(**_):
        raise RuntimeError("nope")
    monkeypatch.setattr(ctools.llm_service, "get_structured_response", _boom, raising=True)

    out = await ctools.draft_character_profile.ainvoke(
        {"character_name": "Lorelei", "category": "Gilmore Girls", **ids}
    )
    assert isinstance(out, CharacterProfile)
    assert out.name == "Lorelei"  # name lock applied on fallback


# ---------------------------------------------------------------------------
# improve_character_profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_improve_character_profile_happy_path(ids):
    existing = {
        "name": "The Analyst",
        "short_description": "old",
        "profile_text": "old text",
    }
    out = await ctools.improve_character_profile.ainvoke(
        {"existing_profile": existing, "feedback": "Make it punchier", **ids}
    )
    assert isinstance(out, CharacterProfile)
    assert out.name == "The Analyst"
    assert out.short_description  # improved by fake llm
    assert out.profile_text


@pytest.mark.asyncio
async def test_improve_character_profile_fallback_on_error(ids, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(ctools.llm_service, "get_structured_response", _boom, raising=True)

    existing = {
        "name": "The Skeptic",
        "short_description": "short",
        "profile_text": "longer text",
        "image_url": "http://x/y.png",
    }
    out = await ctools.improve_character_profile.ainvoke(
        {"existing_profile": existing, "feedback": "n/a", **ids}
    )
    assert out.name == "The Skeptic"
    assert out.short_description == "short"
    assert out.profile_text == "longer text"
    assert out.image_url == "http://x/y.png"


# ---------------------------------------------------------------------------
# generate_baseline_questions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_baseline_questions_normalizes_and_dedupes(ids, monkeypatch):
    async def _fake_gsr(**_):
        # Return duplicates + one with image to verify dedupe and image retention
        return make_question_list_with_dupes()

    monkeypatch.setattr(
        ctools.llm_service, "get_structured_response", _fake_gsr, raising=True
    )

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Cats",
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(),
            # ask the tool to produce exactly 3 so we don't depend on global settings
            "num_questions": 3,
            **ids,
        }
    )
    assert isinstance(out, list)
    assert len(out) == 3
    assert all(isinstance(q, QuizQuestion) for q in out)
    # options should be normalized to unique entries, with reasonable bounds
    assert all(2 <= len(q.options) <= 4 for q in out)

    # First question should have deduped options and preserved image_url for "B"
    first_opts = out[0].options
    texts = [o.get("text") for o in first_opts]
    assert "A" in texts and "B" in texts and "C" in texts
    b = next(o for o in first_opts if o["text"] == "B")
    assert b.get("image_url") == "http://x/img.png"


@pytest.mark.asyncio
async def test_generate_baseline_questions_respects_num_questions_override(ids, monkeypatch):
    # LLM returns 3 questions; tool should trim to num_questions=2
    q1 = QuestionOut(
        question_text="Q1",
        options=[QuestionOption(text="A"), QuestionOption(text="B")]
    )
    q2 = QuestionOut(
        question_text="Q2",
        options=[QuestionOption(text="C"), QuestionOption(text="D")]
    )
    q3 = QuestionOut(
        question_text="Q3",
        options=[QuestionOption(text="E"), QuestionOption(text="F")]
    )
    payload = QuestionList(questions=[q1, q2, q3])

    async def _fake_gsr(**_):
        return payload

    monkeypatch.setattr(
        ctools.llm_service, "get_structured_response", _fake_gsr, raising=True
    )

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Cats",
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(),
            "num_questions": 2,
            **ids,
        }
    )
    assert len(out) == 2
    assert [q.question_text for q in out] == ["Q1", "Q2"]


@pytest.mark.asyncio
async def test_generate_baseline_questions_pads_min_options(ids, monkeypatch):
    # Return a question with only one option; tool should pad to at least 2
    q1 = QuestionOut(
        question_text="Only one?",
        options=[QuestionOption(text="Solo")]
    )
    payload = QuestionList(questions=[q1])

    async def _fake_gsr(**_):
        return payload

    monkeypatch.setattr(
        ctools.llm_service, "get_structured_response", _fake_gsr, raising=True
    )

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Cats",
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(),
            **ids,
        }
    )
    assert len(out) == 1
    opts = out[0].options
    assert len(opts) >= 2
    assert opts[0]["text"] == "Solo"  # original kept


@pytest.mark.asyncio
async def test_generate_baseline_questions_honors_max_options_setting(ids, monkeypatch):
    # Create a single question with 5 options (including dup w/ image)
    payload = QuestionList(questions=[
        QuestionOut(
            question_text="Limit me",
            options=[
                QuestionOption(text="A"),
                QuestionOption(text="B"),
                QuestionOption(text="b", image_url="http://img/b.png"),  # upgrade dup
                QuestionOption(text="C"),
                QuestionOption(text="D"),
            ],
        )
    ])

    async def _fake_gsr(**_): return payload
    monkeypatch.setattr(ctools.llm_service, "get_structured_response", _fake_gsr, raising=True)
    # Force max_options_m = 3 for this test
    monkeypatch.setattr(ctools.settings.quiz, "max_options_m", 3, raising=False)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Anything",
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(),
            "num_questions": 1,
            **ids,
        }
    )
    assert len(out) == 1
    opts = out[0].options
    assert len(opts) == 3
    texts = [o["text"] for o in opts]
    assert "A" in texts and "B" in texts and "C" in texts
    # dedupe should keep image_url for B/b
    assert next(o for o in opts if o["text"] == "B").get("image_url") == "http://img/b.png"


# ---------------------------------------------------------------------------
# generate_next_question
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_next_question_uses_history_and_returns_normalized_question(ids, llm_spy):
    history = [
        {"question_index": 0, "question_text": "Pick one", "answer_text": "A", "option_index": 0}
    ]
    out = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": history,
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(title="Quiz: Cats"),
            **ids,
        }
    )
    assert isinstance(out, QuizQuestion)
    assert out.question_text  # non-empty
    assert len(out.options) >= 2
    assert llm_spy["tool_name"] == "next_question_generator"
    assert getattr(llm_spy["response_model"], "__name__", "") == "QuestionOut"


@pytest.mark.asyncio
async def test_generate_next_question_graceful_fallback_on_llm_error(ids, monkeypatch):
    async def _boom(**_):
        raise RuntimeError("nope")
    monkeypatch.setattr(
        ctools.llm_service, "get_structured_response", _boom, raising=True
    )

    out = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [],
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(),
            **ids,
        }
    )
    assert isinstance(out, QuizQuestion)
    assert "(Unable to generate the next question right now)" in out.question_text
    assert len(out.options) >= 2  # fallback provides two options


# ---------------------------------------------------------------------------
# decide_next_step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decide_next_step_passthrough(ids, llm_spy):
    out = await ctools.decide_next_step.ainvoke(
        {
            "quiz_history": [],
            "character_profiles": [sample_character()],
            "synopsis": sample_synopsis(),
            **ids,
        }
    )
    assert isinstance(out, NextStepDecision)
    assert out.action in {"ASK_ONE_MORE_QUESTION", "FINISH_NOW"}
    assert llm_spy["tool_name"] == "decision_maker"
    assert getattr(llm_spy["response_model"], "__name__", "") == "NextStepDecision"


# ---------------------------------------------------------------------------
# write_final_user_profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_final_user_profile_basic(ids, llm_spy):
    result = await ctools.write_final_user_profile.ainvoke(
        {
            "winning_character": sample_character("The Optimist"),
            "quiz_history": [],
            **ids,
        }
    )
    # The tool returns app.models.api.FinalResult, but we only need to check shape basics.
    assert result.title
    assert llm_spy["tool_name"] == "final_profile_writer"


@pytest.mark.asyncio
async def test_write_final_user_profile_fallback(ids, monkeypatch):
    async def _boom(**_):
        raise RuntimeError("blocked")
    monkeypatch.setattr(ctools.llm_service, "get_structured_response", _boom, raising=True)

    result = await ctools.write_final_user_profile.ainvoke(
        {"winning_character": {"name": "X"}, "quiz_history": [], **ids}
    )
    assert result.title == "We couldn't determine your result"
    assert "Please try again" in result.description


# ---------------------------------------------------------------------------
# Internal helpers: topic analysis & option normalization
# ---------------------------------------------------------------------------

def test__simple_singularize():
    f = ctools._simple_singularize
    assert f("Cats") == "Cat"
    assert f("stories") == "story"
    assert f("buses") == "buse"[:-1]  # "buses" -> s[:-2] -> "bus"
    assert f("glass") == "glass"
    assert f("s") == ""


def test__looks_like_media_title():
    g = ctools._looks_like_media_title
    assert g("Gilmore Girls") is True
    assert g("Star Wars Trilogy") is True
    assert g("Types of Salad") is False  # contains type synonym
    assert g("") is False


def test__analyze_topic_paths():
    a = ctools._analyze_topic("Gilmore Girls")
    assert a["is_media"] is True and a["outcome_kind"] == "characters"
    s = ctools._analyze_topic("MBTI Personality")
    assert s["creativity_mode"] == "factual" and s["outcome_kind"] == "profiles"
    t = ctools._analyze_topic("Cats")
    assert t["outcome_kind"] == "types" and t["normalized_category"].startswith("Type of Cat")
    k = ctools._analyze_topic("types of bread")
    assert k["outcome_kind"] == "types" and k["is_media"] is False


def test__option_to_dict_various_shapes():
    class Pseudo:
        def __init__(self): self.text = "A"; self.image_url = "http://x/a.png"
    assert ctools._option_to_dict("X") == {"text": "X"}
    assert ctools._option_to_dict({"label": "Y"}) == {"text": "Y"}
    assert ctools._option_to_dict(Pseudo()) == {"text": "A", "image_url": "http://x/a.png"}


def test__normalize_options_dedupe_and_max():
    raw = [
        {"text": "A"},
        {"text": "B"},
        {"text": "b", "image_url": "http://img/b.png"},
        {"text": "C"},
    ]
    out = ctools._normalize_options(raw, max_options=2)
    assert [o["text"] for o in out] == ["A", "B"]
    # ensure upgrade on duplicates happened before truncation when not capped
    out2 = ctools._normalize_options(raw, max_options=None)
    b = next(o for o in out2 if o["text"] == "B")
    assert b.get("image_url") == "http://img/b.png"


def test__ensure_min_options_padding_and_filtering():
    start = [{"text": ""}, {"text": "Keep"}]
    out = ctools._ensure_min_options(start, minimum=3)
    assert [o["text"] for o in out][:1] == ["Keep"]
    assert len(out) == 3


def test__ensure_quiz_prefix_variants():
    f = ctools._ensure_quiz_prefix
    assert f("quiz - Cats") == "Quiz: Cats"
    assert f("Quiz — Dogs") == "Quiz: Dogs"
    assert f("") == "Quiz: Untitled"


def test__iter_texts_robust():
    class Obj: text = "T"
    items = ["A", {"label": "B"}, Obj(), None, "  C  "]
    out = list(ctools._iter_texts(items))
    assert out == ["A", "B", "T", "C"]


# ---------------------------------------------------------------------------
# Retrieval helper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test__fetch_character_context_uses_wiki_and_trims(monkeypatch):
    # Use the correct import path
    import app.agent.tools.data_tools as dtools

    # Ensure retrieval is allowed during the test (and prefer Wikipedia only)
    monkeypatch.setattr(
        ctools.settings,
        "retrieval",
        types.SimpleNamespace(policy="all", allow_wikipedia=True, allow_web=False),
        raising=False,
    )

    # The code uses wikipedia_search.invoke(...) via a thread executor
    class StubWiki:
        def invoke(self, payload):  # sync path expected by the code
            return "x" * 1500  # >1200 chars so we can assert trimming

    class StubWeb:
        async def ainvoke(self, payload):
            return "should_not_use"

    # Give the test unlimited budget so the wiki branch actually runs
    monkeypatch.setattr(dtools, "consume_retrieval_slot", lambda *a, **k: True, raising=True)
    monkeypatch.setattr(dtools, "wikipedia_search", StubWiki(), raising=True)
    monkeypatch.setattr(dtools, "web_search", StubWeb(), raising=True)

    out = await ctools._fetch_character_context("Luke", "Star Wars Characters", "t", "s")
    assert isinstance(out, str)
    assert len(out) == 1200  # trimmed
