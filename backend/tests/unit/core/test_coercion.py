"""§19.2 AC-QUALITY-R2-COERCE — unit tests for coerce_to_dict."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.core.coercion import coerce_to_dict


class _Model(BaseModel):
    a: int
    b: str = "x"


def test_none_returns_empty_dict():
    """AC-QUALITY-R2-COERCE-2(a): None -> {}"""
    assert coerce_to_dict(None) == {}


def test_dict_returns_shallow_copy():
    """AC-QUALITY-R2-COERCE-2(b): dict -> shallow copy (not the same object)."""
    src = {"a": 1, "b": [1, 2]}
    result = coerce_to_dict(src)
    assert result == src
    assert result is not src
    # Shallow copy: nested mutables are shared
    assert result["b"] is src["b"]


def test_pydantic_v2_model_dumps():
    """AC-QUALITY-R2-COERCE-2(c): model -> model_dump() output."""
    m = _Model(a=7, b="hello")
    result = coerce_to_dict(m)
    assert result == {"a": 7, "b": "hello"}


def test_unsupported_type_raises_type_error():
    """AC-QUALITY-R2-COERCE-2(d): unknown type -> TypeError, not silent."""
    with pytest.raises(TypeError, match="coerce_to_dict"):
        coerce_to_dict(42)
    with pytest.raises(TypeError):
        coerce_to_dict("hello")
    with pytest.raises(TypeError):
        coerce_to_dict([1, 2, 3])


def test_model_dump_failure_returns_empty_dict_and_logs():
    """AC-QUALITY-R2-COERCE-3: model_dump failures are logged at debug, not raised."""

    class Broken:
        def model_dump(self) -> dict:  # noqa: D401
            raise RuntimeError("kaboom")

    result = coerce_to_dict(Broken())
    assert result == {}


def test_model_dump_non_dict_returns_empty():
    """Defensive: a model_dump that returns a non-dict yields {}."""

    class Weird:
        def model_dump(self):
            return "not a dict"

    assert coerce_to_dict(Weird()) == {}


def test_pydantic_v1_dict_fallback():
    """Legacy Pydantic-v1-style .dict() is honoured."""

    class Legacy:
        def dict(self) -> dict:
            return {"legacy": True}

    assert coerce_to_dict(Legacy()) == {"legacy": True}
