"""Iter J: NextQuestionRequest must reject answers that carry neither
``answer`` text nor ``option_index``, and must reject negative question indices.
Both checks belong at the schema boundary so the route handler never sees
a payload that records an empty answer in quiz history.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.api import NextQuestionRequest


def _payload(**overrides):
    base = {"quiz_id": str(uuid4()), "question_index": 0}
    base.update(overrides)
    return base


def test_rejects_payload_without_answer_or_option_index() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest.model_validate(_payload())


def test_rejects_empty_answer_with_no_option_index() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest.model_validate(_payload(answer=""))


def test_rejects_whitespace_only_answer_with_no_option_index() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest.model_validate(_payload(answer="   "))


def test_rejects_negative_question_index() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest.model_validate(_payload(question_index=-1, answer="x"))


def test_accepts_answer_only() -> None:
    req = NextQuestionRequest.model_validate(_payload(answer="hello"))
    assert req.answer == "hello"


def test_accepts_option_index_only() -> None:
    req = NextQuestionRequest.model_validate(_payload(option_index=2))
    assert req.option_index == 2


def test_accepts_both_answer_and_option_index() -> None:
    req = NextQuestionRequest.model_validate(_payload(answer="hi", option_index=0))
    assert req.option_index == 0
