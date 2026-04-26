"""NextQuestionRequest: cap free-text answer and option_index range."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.api import NextQuestionRequest


def test_short_answer_accepted() -> None:
    r = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, answer="Yes")
    assert r.answer == "Yes"


def test_option_index_accepted() -> None:
    r = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=2)
    assert r.option_index == 2


def test_oversized_answer_rejected() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, answer="x" * 3000)


def test_negative_option_index_rejected() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=-1)


def test_huge_option_index_rejected() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=10_000)


def test_neither_answer_nor_option_rejected() -> None:
    with pytest.raises(ValidationError):
        NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0)


def test_answer_at_exact_limit_accepted() -> None:
    r = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, answer="x" * 2048)
    assert len(r.answer) == 2048
