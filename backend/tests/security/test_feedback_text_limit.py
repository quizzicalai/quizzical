"""Cap free-text feedback comments at 4 KB."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.api import FeedbackRatingEnum, FeedbackRequest


def test_short_text_accepted() -> None:
    fr = FeedbackRequest(quiz_id=uuid.uuid4(), rating=FeedbackRatingEnum.UP, text="Loved it!")
    assert fr.text == "Loved it!"


def test_none_text_accepted() -> None:
    fr = FeedbackRequest(quiz_id=uuid.uuid4(), rating=FeedbackRatingEnum.DOWN)
    assert fr.text is None


def test_oversized_text_rejected() -> None:
    big = "x" * 5000
    with pytest.raises(ValidationError) as exc:
        FeedbackRequest(quiz_id=uuid.uuid4(), rating=FeedbackRatingEnum.UP, text=big)
    msg = str(exc.value).lower()
    assert "max_length" in msg or "at most" in msg


def test_text_at_exact_limit_accepted() -> None:
    at_cap = "x" * 4096
    fr = FeedbackRequest(quiz_id=uuid.uuid4(), rating=FeedbackRatingEnum.UP, text=at_cap)
    assert len(fr.text) == 4096
