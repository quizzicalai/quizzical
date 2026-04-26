"""Iter 6 — DRY refactor of quiz-question coercion.

Both ``_generate_baseline_questions_node`` and ``_generate_adaptive_question_node``
inlined the same shape-normalising helper that converts an LLM tool's loose
output (Pydantic model, dict, or partial) into a strict ``QuizQuestion``.
This file pins the contract for the extracted module-level helper.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.schemas import QuestionOption, QuestionOut, QuizQuestion


def test_quiz_question_from_obj_returns_quiz_question_unchanged() -> None:
    from app.agent.graph import _quiz_question_from_obj

    qq = QuizQuestion(question_text="hi", options=[{"text": "a"}, {"text": "b"}])
    assert _quiz_question_from_obj(qq) is qq


def test_quiz_question_from_obj_coerces_question_out_with_options() -> None:
    from app.agent.graph import _quiz_question_from_obj

    qo = QuestionOut(
        question_text="Pick one",
        options=[
            QuestionOption(text="alpha", image_url="https://example.com/a.png"),
            QuestionOption(text="beta"),
        ],
    )
    out = _quiz_question_from_obj(qo)
    assert isinstance(out, QuizQuestion)
    assert out.question_text == "Pick one"
    opts = [o.model_dump() if hasattr(o, "model_dump") else o for o in out.options]
    assert [o["text"] for o in opts] == ["alpha", "beta"]
    assert opts[0].get("image_url") == "https://example.com/a.png"
    assert opts[1].get("image_url") in (None, "")


def test_quiz_question_from_obj_accepts_plain_dict() -> None:
    from app.agent.graph import _quiz_question_from_obj

    raw: dict[str, Any] = {
        "question_text": "Choose",
        "options": [{"text": "x"}, {"text": "y", "image_url": "https://i/y.jpg"}],
    }
    out = _quiz_question_from_obj(raw)
    assert isinstance(out, QuizQuestion)
    assert out.question_text == "Choose"
    opts = [o.model_dump() if hasattr(o, "model_dump") else o for o in out.options]
    assert opts[1].get("image_url") == "https://i/y.jpg"


def test_quiz_question_from_obj_drops_options_without_text() -> None:
    from app.agent.graph import _quiz_question_from_obj

    out = _quiz_question_from_obj(
        {"question_text": "Q", "options": [{"text": ""}, {"text": "ok"}, {"image_url": "u"}]}
    )
    # Only the option with non-empty text survives the normaliser.
    opts = [o.model_dump() if hasattr(o, "model_dump") else o for o in out.options]
    assert [o["text"] for o in opts] == ["ok"]


def test_quiz_question_from_obj_handles_missing_options() -> None:
    from app.agent.graph import _quiz_question_from_obj

    out = _quiz_question_from_obj({"question_text": "no opts"})
    assert isinstance(out, QuizQuestion)
    assert out.question_text == "no opts"
    assert out.options == []


def test_create_agent_graph_has_return_annotation() -> None:
    """create_agent_graph should advertise a CompiledStateGraph return type."""
    import inspect

    from app.agent.graph import create_agent_graph

    sig = inspect.signature(create_agent_graph)
    assert sig.return_annotation is not inspect.Signature.empty, (
        "create_agent_graph must declare its return type for type checkers."
    )


def test_graph_module_does_not_export_legacy_memorysaver_alias() -> None:
    """The deprecated ``MemorySaver`` alias should not be present on the module.

    LangGraph 1.x renamed it to ``InMemorySaver``; nothing in the codebase
    imports the old name and keeping the alias hides modernization gaps.
    """
    import app.agent.graph as graph_mod

    assert not hasattr(graph_mod, "MemorySaver"), (
        "Drop the legacy MemorySaver alias; callers should use InMemorySaver."
    )


@pytest.mark.asyncio
async def test_baseline_and_adaptive_nodes_use_shared_helper(monkeypatch) -> None:
    """Both generation nodes should funnel tool output through one helper.

    We monkeypatch the helper to a sentinel and verify it's exercised by both
    the baseline and adaptive paths. This guards against future drift where a
    second copy of the coercion logic re-appears.
    """
    from app.agent import graph as graph_mod
    from app.agent.schemas import QuizQuestion

    calls: list[str] = []
    real_helper = graph_mod._quiz_question_from_obj

    def _spy(obj: Any) -> QuizQuestion:
        calls.append(type(obj).__name__)
        return real_helper(obj)

    monkeypatch.setattr(graph_mod, "_quiz_question_from_obj", _spy)

    class _StubTool:
        def __init__(self, ret: Any) -> None:
            self._ret = ret

        async def ainvoke(self, _payload: dict) -> Any:
            return self._ret

    baseline_payload = QuestionOut(
        question_text="b1", options=[QuestionOption(text="x"), QuestionOption(text="y")]
    )

    class _BatchRet:
        questions = [baseline_payload]

    monkeypatch.setattr(
        graph_mod, "tool_generate_baseline_questions", _StubTool(_BatchRet())
    )
    monkeypatch.setattr(
        graph_mod,
        "tool_generate_next_question",
        _StubTool(QuestionOut(question_text="a1", options=[QuestionOption(text="z")])),
    )

    base_state: dict[str, Any] = {
        "session_id": "00000000-0000-0000-0000-000000000000",
        "trace_id": "t",
        "category": "cats",
        "generated_characters": [],
        "synopsis": {"title": "Quiz: Cats", "summary": "s"},
        "topic_analysis": {},
    }

    out_b = await graph_mod._generate_baseline_questions_node(base_state)
    assert out_b.get("baseline_ready") is True
    assert len(calls) >= 1

    calls.clear()
    out_a = await graph_mod._generate_adaptive_question_node(
        {**base_state, "quiz_history": [], "generated_questions": []}
    )
    assert out_a.get("generated_questions")
    assert len(calls) >= 1
