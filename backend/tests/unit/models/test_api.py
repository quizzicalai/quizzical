# backend/tests/unit/models/test_api.py

import uuid

import pytest
from pydantic import ValidationError

from app.models.api import (
    AnswerOption,
    BlendedDimension,
    BlendedProfile,
    CharacterProfile,
    CharactersPayload,
    FeedbackRatingEnum,
    FeedbackRequest,
    FinalResult,
    FrontendStartQuizResponse,
    NextQuestionRequest,
    ProceedRequest,
    ProcessingResponse,
    PydanticGraphState,
    Question,
    QuizQuestion,
    QuizStatusQuestion,
    QuizStatusResult,
    ShareableResultResponse,
    StartQuizPayload,
    StartQuizRequest,
    Synopsis,
)

pytestmark = pytest.mark.unit


def test_character_profile_camelcase_dump():
    """Verify camelCase aliasing on dump."""
    m = CharacterProfile(
        name="The Optimist",
        short_description="Sunny vibes",
        profile_text="You see the bright side.",
    )
    # Default behavior (by_alias=False) uses python names
    dumped_py = m.model_dump()
    assert dumped_py["short_description"] == "Sunny vibes"

    # Alias behavior (by_alias=True) uses camelCase
    dumped = m.model_dump(by_alias=True)
    assert "shortDescription" in dumped
    assert "profileText" in dumped
    assert "imageUrl" in dumped
    # Values preserved
    assert dumped["name"] == "The Optimist"
    assert dumped["shortDescription"] == "Sunny vibes"
    assert dumped["profileText"] == "You see the bright side."
    assert dumped["imageUrl"] is None


def test_question_and_option_carry_image_alt_as_camelcase():
    """REGRESSION (PR #35): generated Q&A images bind an `image_alt`; the API
    models must carry it (serialised `imageAlt`) so a11y alt text reaches the FE
    instead of being silently dropped at the model boundary."""
    opt = AnswerOption(
        text="A fierce dragon over a mountain",
        image_url="https://fal.media/x.png",
        image_alt="A fierce dragon over a mountain — Mythical Creature",
    )
    q = Question(
        text="Which trait fits you?",
        image_url="https://fal.media/q.png",
        image_alt="Establishing scene — Mythical Creature",
        options=[opt],
    )
    dumped = q.model_dump(by_alias=True)
    assert dumped["imageAlt"] == "Establishing scene — Mythical Creature"
    assert dumped["options"][0]["imageAlt"] == (
        "A fierce dragon over a mountain — Mythical Creature"
    )
    # Optional: absent alt serialises as None (older snapshots stay valid).
    bare = AnswerOption(text="x").model_dump(by_alias=True)
    assert bare["imageAlt"] is None


def test_start_quiz_request_explicit_alias_roundtrip():
    """Verify cf-turnstile-response alias handling."""
    data = {
        "category": "Cats",
        "cf-turnstile-response": "tok123",
    }
    # Input with alias key
    req = StartQuizRequest.model_validate(data)
    assert req.category == "Cats"
    assert req.cf_turnstile_response == "tok123"

    # Dump with alias uses hyphenated key
    dumped = req.model_dump(by_alias=True)
    assert dumped["category"] == "Cats"
    assert dumped["cf-turnstile-response"] == "tok123"


def test_start_quiz_payload_discriminated_union_with_synopsis():
    """Verify StartQuizPayload correctly serializes Synopsis variant."""
    syn = Synopsis(title="Quiz: Cats", summary="Felines 101")
    payload = StartQuizPayload(type="synopsis", data=syn)

    dumped = payload.model_dump(by_alias=True)

    # Discriminator at root and inside data
    assert dumped["type"] == "synopsis"
    assert dumped["data"]["type"] == "synopsis"
    assert dumped["data"]["title"] == "Quiz: Cats"
    assert dumped["data"]["summary"] == "Felines 101"

    # Validate back from dumped form
    payload2 = StartQuizPayload.model_validate(dumped)
    assert payload2.type == "synopsis"
    assert isinstance(payload2.data, Synopsis)
    assert payload2.data.title == "Quiz: Cats"


def test_start_quiz_payload_discriminated_union_with_question():
    """Verify StartQuizPayload correctly serializes QuizQuestion variant."""
    q = QuizQuestion(
        question_text="Pick a vibe",
        options=[{"text": "Cozy"}, {"text": "Noir"}],
    )
    # Note: type="question" matches the literal in StartQuizPayload definition
    payload = StartQuizPayload(type="question", data=q)

    dumped = payload.model_dump(by_alias=True)
    assert dumped["type"] == "question"
    assert dumped["data"]["type"] == "question"
    assert dumped["data"]["questionText"] == "Pick a vibe"
    assert dumped["data"]["options"] == [{"text": "Cozy"}, {"text": "Noir"}]

    # Validate back from dumped form
    payload2 = StartQuizPayload.model_validate(dumped)
    assert payload2.type == "question"
    assert isinstance(payload2.data, QuizQuestion)
    assert payload2.data.question_text == "Pick a vibe"


def test_characters_payload_structure():
    """Verify CharactersPayload list wrapper."""
    chars = [
        CharacterProfile(name="A", short_description="s", profile_text="p"),
        CharacterProfile(name="B", short_description="s", profile_text="p"),
    ]
    payload = CharactersPayload(data=chars)
    dumped = payload.model_dump(by_alias=True)

    assert dumped["type"] == "characters"
    assert isinstance(dumped["data"], list)
    assert len(dumped["data"]) == 2
    assert dumped["data"][0]["name"] == "A"


def test_quiz_status_union_variants_and_dump():
    """Verify QuizStatusResponse union (Active vs Finished vs Processing)."""

    # 1. Active (Question)
    q = Question(text="Choose one", options=[AnswerOption(text="A"), AnswerOption(text="B")])
    active = QuizStatusQuestion(status="active", type="question", data=q)

    # 2. Finished (Result)
    res = FinalResult(title="You are The Sage", description="Wise and calm.")
    finished = QuizStatusResult(status="finished", type="result", data=res)

    # 3. Processing
    proc = ProcessingResponse(status="processing", quiz_id=uuid.uuid4())

    # Dump checks
    d_active = active.model_dump(by_alias=True)
    assert d_active["status"] == "active"
    assert d_active["type"] == "question"
    assert d_active["data"]["text"] == "Choose one"

    d_finished = finished.model_dump(by_alias=True)
    assert d_finished["status"] == "finished"
    assert d_finished["type"] == "result"
    assert d_finished["data"]["title"] == "You are The Sage"

    d_proc = proc.model_dump(by_alias=True)
    assert d_proc["status"] == "processing"


def test_final_result_single_character_is_byte_identical():
    """A single-character result must emit NEITHER result_kind NOR profile, so
    its wire payload is byte-for-byte identical to before the blended feature."""
    res = FinalResult(title="You are The Sage", description="Wise and calm.", image_url=None)

    dumped = res.model_dump(by_alias=True)
    assert dumped == {
        "title": "You are The Sage",
        "imageUrl": None,
        "description": "Wise and calm.",
    }
    # No new keys under either spelling.
    assert "resultKind" not in dumped and "result_kind" not in dumped
    assert "profile" not in dumped

    # Nested through the status response too.
    finished = QuizStatusResult(status="finished", type="result", data=res)
    d = finished.model_dump(by_alias=True)
    assert set(d["data"].keys()) == {"title", "imageUrl", "description"}


def test_final_result_blended_emits_kind_and_profile():
    res = FinalResult(
        title="You're a D/C blend",
        description="n" * 400,
        result_kind="blended_profile",
        profile=BlendedProfile(
            dimensions=[BlendedDimension(name="Dominance", emphasis=80, blurb="b")],
            primary="Dominance",
            secondary="Conscientiousness",
            narrative="n" * 400,
        ),
    )
    dumped = res.model_dump(by_alias=True)
    assert dumped["resultKind"] == "blended_profile"
    assert dumped["profile"]["primary"] == "Dominance"
    assert dumped["profile"]["dimensions"][0]["name"] == "Dominance"


def test_shareable_result_blend_aware_serialization():
    # Single-character: no new keys.
    single = ShareableResultResponse(title="T", description="D", image_url=None)
    sd = single.model_dump(by_alias=True)
    assert "resultKind" not in sd and "profile" not in sd

    # Blended: validates a plain profile dict and emits the blend.
    blended = ShareableResultResponse(
        title="T",
        description="D",
        result_kind="blended_profile",
        profile={
            "dimensions": [{"name": "Dominance", "emphasis": 80, "blurb": "b"}],
            "primary": "Dominance",
            "secondary": None,
            "narrative": "n" * 400,
        },
    )
    bd = blended.model_dump(by_alias=True)
    assert bd["resultKind"] == "blended_profile"
    assert bd["profile"]["primary"] == "Dominance"


def test_pydantic_graph_state_allows_extras_and_preserves_core():
    """Verify PydanticGraphState handles extra keys and camelCase dumping."""
    sid = uuid.uuid4()
    state = PydanticGraphState(
        session_id=sid,
        trace_id="t-abc",
        category="Cats",
        messages=[{"type": "human", "content": "start"}],
        # Known coordination fields
        baseline_count=2,
        ready_for_questions=True,
        # Extra fields (should be allowed due to extra="allow")
        should_finalize=True,
        someNewFlag="ok",
    )

    # Core fields access
    assert state.session_id == sid
    assert state.trace_id == "t-abc"
    assert state.category == "Cats"
    assert state.messages[0]["type"] == "human"
    assert state.baseline_count == 2
    assert state.ready_for_questions is True

    # Extra fields access via attribute (if model allows) or dict
    # Pydantic v2 stores extras in __pydantic_extra__
    assert state.should_finalize is True # type: ignore[attr-defined]

    # Dump behavior
    dumped_alias = state.model_dump(by_alias=True)

    # Core fields are camelCase
    assert "sessionId" in dumped_alias
    assert "traceId" in dumped_alias
    assert "readyForQuestions" in dumped_alias
    assert "baselineCount" in dumped_alias

    # Extra fields are preserved as-is (not automatically camelized)
    assert "should_finalize" in dumped_alias
    assert dumped_alias["should_finalize"] is True
    assert "someNewFlag" in dumped_alias
    assert dumped_alias["someNewFlag"] == "ok"


def test_frontend_start_quiz_response_optional_payloads():
    """Verify FrontendStartQuizResponse handles None payloads."""
    quiz_id = uuid.uuid4()
    # Minimal init
    resp = FrontendStartQuizResponse(quiz_id=quiz_id)

    dumped = resp.model_dump(by_alias=True)
    assert dumped["quizId"] == quiz_id
    assert dumped["initialPayload"] is None
    assert dumped["charactersPayload"] is None


def test_next_question_request_validation():
    """Verify NextQuestionRequest validation and aliasing."""
    quiz_id = uuid.uuid4()

    # Input with snake_case
    next_req = NextQuestionRequest(
        quiz_id=quiz_id,
        question_index=3,
        answer="A",
        option_index=0
    )

    dumped = next_req.model_dump(by_alias=True)
    assert dumped["quizId"] == quiz_id
    assert dumped["questionIndex"] == 3
    assert dumped["answer"] == "A"
    assert dumped["optionIndex"] == 0


def test_feedback_request_enum_validation():
    """Verify FeedbackRequest validates rating enum."""
    quiz_id = uuid.uuid4()

    # Valid UP
    req1 = FeedbackRequest(quiz_id=quiz_id, rating="up")
    assert req1.rating == FeedbackRatingEnum.UP

    # Valid DOWN
    req2 = FeedbackRequest(quiz_id=quiz_id, rating="down")
    assert req2.rating == FeedbackRatingEnum.DOWN

    # Invalid
    with pytest.raises(ValidationError):
        FeedbackRequest(quiz_id=quiz_id, rating="meh")


def test_proceed_request_structure():
    """Verify ProceedRequest structure."""
    quiz_id = uuid.uuid4()
    p = ProceedRequest(quiz_id=quiz_id)
    dumped = p.model_dump(by_alias=True)
    assert dumped["quizId"] == quiz_id
