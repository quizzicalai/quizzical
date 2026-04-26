"""Iter C — hoist ``import random`` out of redis_cache._jittered_backoff
and lock down its bounds.

Issue: ``app/services/redis_cache.py::_jittered_backoff`` performs
``import random`` inside the function body, which runs on every cache
WATCH conflict retry. Moves the import to module scope and adds a
deterministic test that pins the documented bounds (linear base * attempt,
capped at ``cap``, plus jitter < 0.01s).
"""

from __future__ import annotations

import ast
import pathlib

import pytest


def test_jittered_backoff_has_no_inner_import() -> None:
    from app.services import redis_cache

    src = pathlib.Path(redis_cache.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_jittered_backoff"
    )
    inner = [
        n
        for n in ast.walk(target)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert not inner, (
        f"_jittered_backoff must not import inside the function body; found: "
        f"{[ast.dump(n) for n in inner]}"
    )


def test_jittered_backoff_module_imports_random() -> None:
    """``random`` must be a module-level import of redis_cache."""
    from app.services import redis_cache

    assert hasattr(redis_cache, "random"), (
        "redis_cache should expose `random` as a module-level import (hoisted "
        "out of _jittered_backoff)."
    )


@pytest.mark.parametrize(
    "attempt, expected_min",
    [
        (1, 0.05),
        (2, 0.10),
        (5, 0.25),
        (10, 0.50),  # capped at 0.5
        (100, 0.50),  # still capped
    ],
)
def test_jittered_backoff_bounds(attempt: int, expected_min: float) -> None:
    """Backoff is ``min(cap, base * max(1, attempt)) + jitter`` where
    jitter is in ``[0, 0.01)``. Verify the deterministic floor and the
    bounded ceiling.
    """
    from app.services.redis_cache import _jittered_backoff

    value = _jittered_backoff(attempt)
    assert value >= expected_min, f"attempt={attempt} value={value} < {expected_min}"
    assert value < expected_min + 0.01, (
        f"attempt={attempt} value={value} exceeds floor + jitter cap"
    )


def test_jittered_backoff_attempt_zero_uses_base() -> None:
    """``max(1, attempt)`` means attempt=0 still produces base * 1."""
    from app.services.redis_cache import _jittered_backoff

    value = _jittered_backoff(0)
    assert 0.05 <= value < 0.06
