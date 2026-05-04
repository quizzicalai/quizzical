"""Unit tests for ``scripts/prod_render_smoke``.

Stubs the HTTP layer with httpx's mock transport so the test is
deterministic and offline-safe. Tests are sync because ``main()`` spins
up its own asyncio event loop via ``asyncio.run``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from scripts import prod_render_smoke


# ---------------------------------------------------------------------------
# Mock-transport helper
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory state machine simulating the live API for one walk.

    - /quiz/start returns a synopsis + characters and a fresh quiz_id.
    - /quiz/proceed flips ``ready=True``.
    - /quiz/status returns 'processing' once, then a question, then a result.
    - /quiz/next advances to the next question.
    - /quiz/{id}/media returns a snapshot with image URLs.
    - /topics/suggest returns a configured map.
    """

    def __init__(
        self,
        *,
        suggest_results: dict[str, list[dict[str, str]]] | None = None,
        question_count: int = 2,
        media_image_count: int = 3,
        packs_published: int = 600,
    ) -> None:
        self.quiz_id = str(uuid.uuid4())
        self.suggest_results = suggest_results or {}
        self.question_count = question_count
        self.media_image_count = media_image_count
        self.packs_published = packs_published
        self.answered: int = 0
        self.poll_count: int = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/api/v1/healthz/precompute":
            return httpx.Response(
                200,
                json={
                    "packs_published": self.packs_published,
                    "hits_24h": 0,
                    "misses_24h": 0,
                    "hit_rate_24h": 0.0,
                    "miss_rate_24h": 0.0,
                    "top_misses_24h": [],
                },
            )

        if path == "/api/v1/topics/suggest":
            q = request.url.params.get("q", "")
            return httpx.Response(200, json={"results": self.suggest_results.get(q, [])})

        if path == "/api/v1/quiz/start":
            body = json.loads(request.content or b"{}")
            assert body.get("category"), "category missing in /quiz/start payload"
            assert body.get("cf-turnstile-response"), "turnstile token missing"
            return httpx.Response(
                201,
                json={
                    "quizId": self.quiz_id,
                    "initialPayload": {
                        "type": "synopsis",
                        "data": {
                            "type": "synopsis",
                            "title": "Test Synopsis",
                            "summary": "A pre-populated synopsis body.",
                        },
                    },
                    "charactersPayload": {
                        "type": "characters",
                        "data": [
                            {
                                "name": "Alpha",
                                "shortDescription": "first",
                                "profileText": "long alpha text",
                                "imageUrl": None,
                            },
                            {
                                "name": "Beta",
                                "shortDescription": "second",
                                "profileText": "long beta text",
                                "imageUrl": None,
                            },
                        ],
                    },
                },
            )

        if path == "/api/v1/quiz/proceed":
            return httpx.Response(202, json={"status": "processing", "quizId": self.quiz_id})

        if path == "/api/v1/quiz/next":
            self.answered += 1
            return httpx.Response(202, json={"status": "processing", "quizId": self.quiz_id})

        if path.startswith("/api/v1/quiz/status/"):
            self.poll_count += 1
            if self.answered >= self.question_count:
                return httpx.Response(
                    200,
                    json={
                        "status": "finished",
                        "type": "result",
                        "data": {
                            "title": "You are Alpha",
                            "description": "Result description body.",
                            "image_url": None,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "active",
                    "type": "question",
                    "data": {
                        "text": f"Question {self.answered + 1}",
                        "image_url": None,
                        "options": [
                            {"text": "A", "image_url": None},
                            {"text": "B", "image_url": None},
                            {"text": "C", "image_url": None},
                            {"text": "D", "image_url": None},
                        ],
                    },
                },
            )

        if path.endswith("/media") and "/quiz/" in path:
            chars = [
                {"name": f"C{i}", "imageUrl": f"https://cdn.example/{i}.png"}
                for i in range(self.media_image_count)
            ]
            return httpx.Response(
                200,
                json={
                    "quizId": self.quiz_id,
                    "synopsisImageUrl": None,
                    "resultImageUrl": None,
                    "characters": chars,
                },
            )

        return httpx.Response(404, json={"detail": f"unmocked path {path}"})


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch, backend: _FakeBackend
) -> None:
    transport = httpx.MockTransport(backend.handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        prod_render_smoke.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_walk_succeeds_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(
        suggest_results={
            "hogwarts": [{"slug": "hogwarts-house", "display_name": "Hogwarts House"}]
        },
        question_count=2,
        media_image_count=3,
    )
    _patch_async_client(monkeypatch, backend)
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "hogwarts-house",
            "--max-poll-s",
            "5",
            "--poll-interval-s",
            "0.0",
        ]
    )
    assert rc == 0
    assert backend.answered == 2


def test_walk_fails_when_synopsis_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/quiz/start":
            return httpx.Response(
                201,
                json={
                    "quizId": backend.quiz_id,
                    "initialPayload": {
                        "type": "synopsis",
                        "data": {"type": "synopsis", "title": "", "summary": ""},
                    },
                    "charactersPayload": {
                        "type": "characters",
                        "data": [
                            {
                                "name": "X",
                                "shortDescription": "x",
                                "profileText": "x",
                            }
                        ],
                    },
                },
            )
        return backend.handle(request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        prod_render_smoke.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "broken",
            "--max-poll-s",
            "1",
            "--poll-interval-s",
            "0.0",
        ]
    )
    assert rc == 1


def test_walk_fails_when_no_character_images(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(media_image_count=0)
    _patch_async_client(monkeypatch, backend)
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "no-art",
            "--max-poll-s",
            "5",
            "--poll-interval-s",
            "0.0",
        ]
    )
    assert rc == 1


def test_allow_missing_images_flag_passes_when_no_character_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend(media_image_count=0)
    _patch_async_client(monkeypatch, backend)
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "no-art",
            "--max-poll-s",
            "5",
            "--poll-interval-s",
            "0.0",
            "--allow-missing-images",
        ]
    )
    assert rc == 0


def test_walk_times_out_when_status_stays_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/quiz/status/"):
            return httpx.Response(
                200, json={"status": "processing", "quizId": backend.quiz_id}
            )
        return backend.handle(request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        prod_render_smoke.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "stuck",
            "--max-poll-s",
            "0.05",
            "--poll-interval-s",
            "0.01",
        ]
    )
    assert rc == 1


def test_preflight_min_packs_gate_blocks_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend(packs_published=10)
    _patch_async_client(monkeypatch, backend)
    monkeypatch.setenv("OPERATOR_TOKEN", "x" * 48)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "hogwarts-house",
            "--min-packs",
            "500",
            "--max-poll-s",
            "5",
            "--poll-interval-s",
            "0.0",
        ]
    )
    assert rc == 1


def test_walk_fails_on_http_error_from_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/quiz/start":
            return httpx.Response(503, text="upstream down")
        return httpx.Response(404, text="x")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        prod_render_smoke.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    rc = prod_render_smoke.main(
        [
            "--api-url",
            "http://test",
            "--slugs",
            "any",
            "--max-poll-s",
            "1",
            "--poll-interval-s",
            "0.0",
        ]
    )
    assert rc == 1
