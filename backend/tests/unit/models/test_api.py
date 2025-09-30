# backend/tests/unit/models/test_api.py

import uuid
import pytest

from app.models.api import (
    APIBaseModel,
    Synopsis,
    CharacterProfile,
    AnswerOption,
    Question,
    QuizQuestion,
    FinalResult,
    StartQuizRequest,
    StartQuizPayload,
    CharactersPayload,
    FrontendStartQuizResponse,
    NextQuestionRequest,
    ProceedRequest,
    ProcessingResponse,
    QuizStatusQuestion,
    QuizStatusResult,
    QuizStatusResponse,
    PydanticGraphState,
)


def test_character_profile_camelcase_dump():
    m = CharacterProfile(
        name="The Optimist",
        short_description="Sunny vibes",
        profile_text="You see the bright side.",
    )
    dumped = m.model_dump(by_alias=True)
    # Keys should be camelCase for declared fields
    assert "shortDescription" in dumped
    assert "profileText" in dumped
    assert "imageUrl" in dumped
    # Values preserved
    assert dumped["name"] == "The Optimist"
    assert dumped["shortDescription"] == "Sunny vibes"
    assert dumped["profileText"] == "You see the bright side."
    assert dumped["imageUrl"] is None


def test_start_quiz_request_explicit_alias_roundtrip():
    data = {
        "category": "Cats",
        "cf-turnstile-response": "tok123",
    }
    req = StartQuizRequest.model_validate(data)
    assert req.category == "Cats"
    # Explicit alias should be honored on dump
    dumped = req.model_dump(by_alias=True)
    assert dumped["category"] == "Cats"
    assert dumped["cf-turnstile-response"] == "tok123"


def test_start_quiz_payload_discriminated_union_with_synopsis():
    syn = Synopsis(title="Quiz: Cats", summary="Felines 101")
    payload = StartQuizPayload(type="synopsis", data=syn)
    dumped = payload.model_dump(by_alias=True)
    # Discriminator carried through
    assert dumped["type"] == "synopsis"
    assert dumped["data"]["type"] == "synopsis"
    assert dumped["data"]["title"] == "Quiz: Cats"
    assert dumped["data"]["summary"] == "Felines 101"

    # Validate back from dumped form
    payload2 = StartQuizPayload.model_validate(dumped)
    assert isinstance(payload2.data, Synopsis)
    assert payload2.data.title == "Quiz: Cats"


def test_start_quiz_payload_discriminated_union_with_question():
    q = QuizQuestion(
        question_text="Pick a vibe",
        options=[{"text": "Cozy"}, {"text": "Noir"}],
    )
    payload = StartQuizPayload(type="question", data=q)
    dumped = payload.model_dump(by_alias=True)
    assert dumped["type"] == "question"
    assert dumped["data"]["type"] == "question"
    assert dumped["data"]["questionText"] == "Pick a vibe"
    assert dumped["data"]["options"] == [{"text": "Cozy"}, {"text": "Noir"}]

    # Validate back from dumped form
    payload2 = StartQuizPayload.model_validate(dumped)
    assert isinstance(payload2.data, QuizQuestion)
    assert payload2.data.question_text == "Pick a vibe"
    assert payload2.data.options[0]["text"] == "Cozy"


def test_quiz_status_union_variants_and_dump():
    # Active/question status
    q = Question(text="Choose one", options=[AnswerOption(text="A"), AnswerOption(text="B")])
    active = QuizStatusQuestion(status="active", type="question", data=q)
    # Finished/result status
    res = FinalResult(title="You are The Sage", description="Wise and calm.")
    finished = QuizStatusResult(status="finished", type="result", data=res)

    # Type-check union accepts both
    def accept(x: QuizStatusResponse) -> QuizStatusResponse:
        return x

    assert accept(active) is active
    assert accept(finished) is finished

    d_active = active.model_dump(by_alias=True)
    assert d_active["status"] == "active"
    assert d_active["type"] == "question"
    assert d_active["data"]["text"] == "Choose one"
    assert isinstance(d_active["data"]["options"], list)

    d_finished = finished.model_dump(by_alias=True)
    assert d_finished["status"] == "finished"
    assert d_finished["type"] == "result"
    assert d_finished["data"]["title"] == "You are The Sage"
    assert d_finished["data"]["description"] == "Wise and calm."


def test_pydantic_graph_state_allows_extras_and_preserves_core():
    sid = uuid.uuid4()
    state = PydanticGraphState(
        session_id=sid,
        trace_id="t-abc",
        category="Cats",
        messages=[{"type": "human", "content": "start"}],
        # Known coordination fields
        baseline_count=2,
        ready_for_questions=True,
        # Extra fields should be allowed/preserved
        **{"should_finalize": True, "someNewFlag": "ok"},
    )
    # Core fields
    assert state.session_id == sid
    assert state.trace_id == "t-abc"
    assert state.category == "Cats"
    assert state.messages and state.messages[0]["type"] == "human"
    assert state.baseline_count == 2
    assert state.ready_for_questions is True

    # Extras preserved (extra="allow")
    dumped = state.model_dump()
    assert dumped.get("should_finalize") is True
    assert dumped.get("someNewFlag") == "ok"

    # Alias dump uses camelCase for declared fields only.
    # Extras are preserved but NOT camelized by Pydantic.
    dumped_alias = state.model_dump(by_alias=True)
    assert "sessionId" in dumped_alias
    assert "traceId" in dumped_alias
    assert "readyForQuestions" in dumped_alias
    assert "baselineCount" in dumped_alias

    # Extras remain as originally provided
    assert "should_finalize" in dumped_alias
    assert dumped_alias["should_finalize"] is True
    assert "someNewFlag" in dumped_alias
    assert dumped_alias["someNewFlag"] == "ok"


def test_frontend_start_quiz_response_optional_payloads():
    quiz_id = uuid.uuid4()
    resp = FrontendStartQuizResponse(
        quiz_id=quiz_id,
        initial_payload=None,
        characters_payload=None,
    )
    dumped = resp.model_dump(by_alias=True)
    assert dumped["quizId"] == quiz_id  # UUID object is fine in model_dump
    assert dumped.get("initialPayload") is None
    assert dumped.get("charactersPayload") is None


def test_requests_basic_validation_and_dump():
    quiz_id = uuid.uuid4()
    next_req = NextQuestionRequest(quiz_id=quiz_id, question_index=3, answer="A", option_index=0)
    dumped_next = next_req.model_dump(by_alias=True)
    assert dumped_next["quizId"] == quiz_id
    assert dumped_next["questionIndex"] == 3
    assert dumped_next["answer"] == "A"
    assert dumped_next["optionIndex"] == 0

    proceed = ProceedRequest(quiz_id=quiz_id)
    dumped_proc = proceed.model_dump(by_alias=True)
    assert dumped_proc["quizId"] == quiz_id


def test_processing_response_dump():
    quiz_id = uuid.uuid4()
    pr = ProcessingResponse(status="processing", quiz_id=quiz_id)
    d = pr.model_dump(by_alias=True)
    assert d["status"] == "processing"
    assert d["quizId"] == quiz_id
