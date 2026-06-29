"""P1 — /status rebuilds live state from Postgres when Redis has expired/evicted.

Previously get_quiz_status read only Redis (1h TTL) and 404'd on a miss,
permanently losing a paused or finished quiz even though the durable
session_history / session_questions rows still held everything.
"""
from __future__ import annotations

import uuid

import pytest

from app.api.endpoints import quiz as quiz_module


class _FakeHistory:
    category = "Cats"
    category_synopsis = {"title": "Quiz: Cats", "summary": "A fun quiz."}
    character_set = [
        {"name": "Alpha", "short_description": "", "profile_text": "x", "image_url": None}
    ]
    final_result = None
    qa_history: list = []


class _FakeSessionQuestions:
    baseline_questions = {
        "questions": [
            {"question_text": "Q1?", "options": [{"text": "a"}, {"text": "b"}]},
            {"question_text": "Q2?", "options": [{"text": "c"}, {"text": "d"}]},
        ]
    }
    adaptive_questions = {"questions": [{"question_text": "Q3?", "options": [{"text": "e"}, {"text": "f"}]}]}


_DEFAULT_HISTORY = _FakeHistory()
_DEFAULT_SQ = _FakeSessionQuestions()


class _FakeDB:
    def __init__(self, *, history=_DEFAULT_HISTORY, sq=_DEFAULT_SQ):
        self._history = history
        self._sq = sq

    async def get(self, model, _qid):
        from app.models.db import SessionHistory, SessionQuestions

        if model is SessionHistory:
            return self._history
        if model is SessionQuestions:
            return self._sq
        return None


@pytest.mark.asyncio
async def test_rehydrate_reconstructs_state_from_db():
    state = await quiz_module._rehydrate_state_from_db(_FakeDB(), uuid.uuid4())
    assert state is not None
    assert state["category"] == "Cats"
    assert state["synopsis"]["title"] == "Quiz: Cats"
    assert len(state["generated_characters"]) == 1
    assert state["baseline_count"] == 2
    assert state["baseline_ready"] is True
    assert len(state["generated_questions"]) == 3  # 2 baseline + 1 adaptive
    assert state["ready_for_questions"] is True
    assert state.get("final_result") is None


@pytest.mark.asyncio
async def test_rehydrate_returns_none_when_no_db_row():
    db = _FakeDB(history=None)
    assert await quiz_module._rehydrate_state_from_db(db, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_rehydrate_finished_quiz_includes_final_result():
    hist = _FakeHistory()
    hist.final_result = {"title": "You are Alpha", "description": "x" * 400}
    state = await quiz_module._rehydrate_state_from_db(_FakeDB(history=hist), uuid.uuid4())
    assert state is not None
    assert state["final_result"]["title"] == "You are Alpha"
