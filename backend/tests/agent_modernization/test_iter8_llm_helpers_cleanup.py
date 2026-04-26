"""Iter 8 — clean up ``invoke_structured`` API.

Two best-practice issues:

1. ``assert isinstance(result, response_model)`` runs in production but is
   wrapped in a bare ``try/except: pass`` — making it both a Bandit B101
   violation and a no-op. Replace with a real type check that produces a
   useful error when the LLM service returns the wrong type.
2. ``schema_kwargs`` is dead API surface — accepted, logged as "ignored",
   never used. Remove it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import BaseModel


class _SampleModel(BaseModel):
    name: str


def test_invoke_structured_no_schema_kwargs_param() -> None:
    from app.agent.llm_helpers import invoke_structured

    sig = inspect.signature(invoke_structured)
    assert "schema_kwargs" not in sig.parameters, (
        "schema_kwargs is dead API surface — drop it instead of logging 'ignored'."
    )


def test_no_assert_statements_in_llm_helpers() -> None:
    """Production code should not use bare ``assert`` for runtime validation."""
    import ast
    import pathlib

    src = pathlib.Path(
        inspect.getfile(__import__("app.agent.llm_helpers", fromlist=["_"]))
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    assert not asserts, (
        f"llm_helpers.py contains {len(asserts)} assert statement(s) — "
        "production code must use explicit checks (Bandit B101)."
    )


@pytest.mark.asyncio
async def test_invoke_structured_raises_typeerror_on_wrong_response_type(monkeypatch) -> None:
    """When LLM returns a non-matching type, raise a clear TypeError instead
    of swallowing it inside a try/except: pass."""
    from app.agent import llm_helpers

    class _FakeService:
        async def get_structured_response(self, **kwargs: Any) -> Any:
            return {"name": "wrong type"}  # not a _SampleModel instance

    monkeypatch.setattr(llm_helpers, "llm_service", _FakeService())
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda _t: {})

    with pytest.raises(TypeError, match="_SampleModel"):
        await llm_helpers.invoke_structured(
            tool_name="t",
            messages=[],
            response_model=_SampleModel,
        )


@pytest.mark.asyncio
async def test_invoke_structured_passes_through_when_type_matches(monkeypatch) -> None:
    from app.agent import llm_helpers

    expected = _SampleModel(name="ok")

    class _FakeService:
        async def get_structured_response(self, **kwargs: Any) -> Any:
            return expected

    monkeypatch.setattr(llm_helpers, "llm_service", _FakeService())
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda _t: {})

    result = await llm_helpers.invoke_structured(
        tool_name="t", messages=[], response_model=_SampleModel
    )
    assert result is expected
