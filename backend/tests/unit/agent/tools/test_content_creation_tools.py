import pytest

from app.agent.tools import content_creation_tools as ctools
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
