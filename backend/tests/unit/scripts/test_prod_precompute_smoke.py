"""Unit tests for ``scripts/prod_precompute_smoke``.

We stub the HTTP layer with httpx's mock transport so the test is
deterministic and offline-safe. Tests are sync because ``main()`` spins
up its own asyncio event loop via ``asyncio.run``.
"""

from __future__ import annotations

import httpx
import pytest

from scripts import prod_precompute_smoke


def _build_transport(
    *, packs_published: int, suggest_results: dict[str, list[dict[str, str]]]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == prod_precompute_smoke.HEALTHZ_PATH:
            assert request.headers.get("Authorization", "").startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "packs_published": packs_published,
                    "hits_24h": 0,
                    "misses_24h": 0,
                    "hit_rate_24h": 0.0,
                    "miss_rate_24h": 0.0,
                    "top_misses_24h": [],
                },
            )
        if request.url.path == prod_precompute_smoke.SUGGEST_PATH:
            q = request.url.params.get("q", "")
            return httpx.Response(200, json={"results": suggest_results.get(q, [])})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        prod_precompute_smoke.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )


def test_smoke_passes_when_packs_meet_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "x" * 48)
    transport = _build_transport(
        packs_published=600,
        suggest_results={"hogwarts": [{"slug": "hogwarts-house", "display_name": "Hogwarts House"}]},
    )
    _patch_async_client(monkeypatch, transport)

    rc = prod_precompute_smoke.main(
        [
            "--api-url",
            "http://test",
            "--min-packs",
            "500",
            "--sample-slugs",
            "hogwarts-house",
        ]
    )
    assert rc == 0


def test_smoke_fails_when_packs_below_threshold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "x" * 48)
    transport = _build_transport(packs_published=10, suggest_results={})
    _patch_async_client(monkeypatch, transport)

    rc = prod_precompute_smoke.main(
        ["--api-url", "http://test", "--min-packs", "500"]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "below required 500" in captured.err


def test_smoke_fails_when_token_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    rc = prod_precompute_smoke.main(["--api-url", "http://test", "--min-packs", "1"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "OPERATOR_TOKEN" in captured.err


def test_smoke_fails_when_sample_slug_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "x" * 48)
    transport = _build_transport(packs_published=600, suggest_results={"hogwarts": []})
    _patch_async_client(monkeypatch, transport)

    rc = prod_precompute_smoke.main(
        [
            "--api-url",
            "http://test",
            "--min-packs",
            "1",
            "--sample-slugs",
            "hogwarts-house",
        ]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "hogwarts-house" in captured.err
