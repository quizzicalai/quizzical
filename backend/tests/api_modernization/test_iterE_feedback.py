"""Iter E — feedback endpoint returns 422 on invalid payloads, not 500.

The current handler in ``app/api/endpoints/feedback.py`` does::

    try:                                       # Pydantic v2
        feedback = FeedbackRequest.model_validate(body)
    except AttributeError:                     # Pydantic v1
        feedback = FeedbackRequest.parse_obj(body)

This is a leftover v1/v2 compat shim, but ``model_validate`` on Pydantic
v2 raises ``ValidationError`` on bad input, not ``AttributeError``. The
``ValidationError`` therefore propagates up to the generic
``except Exception as e`` handler, which logs the failure and returns
**500 Internal Server Error**. This is wrong on two counts:

* It's an HTTP contract violation \u2014 client-side schema errors must be
  4xx (typically 422), not 5xx.
* It pollutes operational metrics with fake server errors and wakes
  on-call for what are actually bad requests.

Pydantic 2 is required (see ``pyproject.toml``). Drop the dead v1
fallback and explicitly map ``ValidationError`` to HTTP 422.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def feedback_app(monkeypatch):
    from app.api import dependencies as deps
    from app.api.endpoints import feedback as feedback_module

    async def _no_db():
        # Endpoint should never reach the DB on a 422 path.
        class _Sess:
            async def commit(self) -> None:  # pragma: no cover
                return None

            async def rollback(self) -> None:  # pragma: no cover
                return None

        yield _Sess()

    async def _bypass_turnstile(*_a: Any, **_kw: Any) -> bool:
        return True

    app = FastAPI()
    app.include_router(feedback_module.router, prefix="/api")
    app.dependency_overrides[deps.get_db_session] = _no_db
    app.dependency_overrides[deps.verify_turnstile] = _bypass_turnstile
    return app


def test_feedback_invalid_payload_returns_422(feedback_app) -> None:
    client = TestClient(feedback_app)

    # `quiz_id` missing entirely \u2014 schema must reject.
    resp = client.post(
        "/api/feedback",
        json={"rating": "up", "cf-turnstile-response": "x"},
    )
    assert resp.status_code == 422, (
        f"expected 422 for missing quiz_id; got {resp.status_code} body={resp.text!r}"
    )


def test_feedback_invalid_rating_returns_422(feedback_app) -> None:
    client = TestClient(feedback_app)

    resp = client.post(
        "/api/feedback",
        json={
            "quiz_id": str(uuid4()),
            "rating": "sideways",  # not a member of FeedbackRatingEnum
            "cf-turnstile-response": "x",
        },
    )
    assert resp.status_code == 422, (
        f"expected 422 for bad rating enum; got {resp.status_code} body={resp.text!r}"
    )


def test_feedback_module_drops_dead_pydantic_v1_fallback() -> None:
    """The ``except AttributeError: ... parse_obj`` shim must be gone."""
    import pathlib

    from app.api.endpoints import feedback as feedback_module

    src = pathlib.Path(feedback_module.__file__).read_text(encoding="utf-8")
    assert "parse_obj" not in src, (
        "Pydantic v1 fallback should be removed; only model_validate is used"
    )
    assert "except AttributeError" not in src, (
        "Dead v1/v2 compat except clause should be removed"
    )
