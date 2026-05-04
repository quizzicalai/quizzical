"""Unit tests for ``scripts/promote_user_quizzes``.

Stubs the candidates-fetch HTTP call with httpx mock transport. The
evaluator and archive builder run for real (they're pure-Python and
deterministic) so we exercise the full happy + sad paths end-to-end
without needing a live API.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest

from scripts import promote_user_quizzes


SECRET = "promote-test-secret-" + "x" * 48


def _full_candidate(slug: str = "brand-new-alpha") -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "category": "Brand New Alpha",
        "completed_at": "2026-01-01T00:00:00+00:00",
        "slug": slug,
        "display_name": "Brand New Alpha",
        "synopsis": {
            "title": "Which Alpha Are You?",
            "summary": "An evergreen sorter for alpha-flavoured archetypes.",
        },
        "characters": [
            {
                "name": f"Char {i}",
                "short_description": f"short {i}",
                "profile_text": f"long profile body for char {i}, multi sentence.",
            }
            for i in range(4)
        ],
        "baseline_questions": [
            {
                "question_text": f"Question {i + 1}?",
                "options": [{"text": f"Opt {i}-{j}"} for j in range(4)],
            }
            for i in range(5)
        ],
        "final_result": {"title": "You are Alpha-1", "description": "Nice."},
        "judge_plan_score": 9,
        "user_sentiment": None,
    }


def _broken_candidate() -> dict:
    """Fails evaluator: one character only and one question only."""
    base = _full_candidate(slug="broken-topic")
    base["characters"] = base["characters"][:1]
    base["baseline_questions"] = base["baseline_questions"][:1]
    return base


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch, candidates: list[dict]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == promote_user_quizzes.PROMOTION_CANDIDATES_PATH:
            assert request.headers.get("Authorization", "").startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "candidates": candidates,
                    "total": len(candidates),
                    "since_hours": 24,
                },
            )
        return httpx.Response(404, text="unmocked")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        promote_user_quizzes.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "z" * 48)
    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", SECRET)


def test_writes_archive_when_candidates_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    _patch_async_client(monkeypatch, [_full_candidate()])

    out_path = tmp_path / "promoted.json"
    rc = promote_user_quizzes.main(
        [
            "--api-url",
            "http://test",
            "--out",
            str(out_path),
        ]
    )
    assert rc == promote_user_quizzes.EXIT_OK
    assert out_path.exists()
    sig_path = out_path.with_suffix(out_path.suffix + ".sig")
    assert sig_path.exists()
    src_path = out_path.with_name(out_path.stem + ".source.json")
    assert src_path.exists()

    # Archive payload is verifiable with the same secret.
    from scripts.import_packs import verify_signature

    assert verify_signature(
        out_path.read_bytes(),
        sig_path.read_text(encoding="utf-8").strip(),
        secret=SECRET,
    )

    # And the archive parses to the expected shape.
    archive = json.loads(out_path.read_bytes())
    assert len(archive["packs"]) == 1
    assert archive["packs"][0]["topic"]["slug"] == "brand-new-alpha"
    assert archive["packs"][0]["built_in_env"] == "promoted"


def test_returns_2_when_no_candidates_pass_evaluator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    _patch_async_client(monkeypatch, [_broken_candidate()])

    out_path = tmp_path / "promoted.json"
    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(out_path)]
    )
    assert rc == promote_user_quizzes.EXIT_NO_CANDIDATES
    assert not out_path.exists()


def test_returns_2_when_api_returns_no_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    _patch_async_client(monkeypatch, [])

    out_path = tmp_path / "promoted.json"
    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(out_path)]
    )
    assert rc == promote_user_quizzes.EXIT_NO_CANDIDATES
    assert not out_path.exists()


def test_fails_when_token_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", SECRET)

    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(tmp_path / "x.json")]
    )
    assert rc == promote_user_quizzes.EXIT_FAIL
    assert "OPERATOR_TOKEN" in capsys.readouterr().err


def test_fails_when_secret_too_short(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "z" * 48)
    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", "tooshort")

    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(tmp_path / "x.json")]
    )
    assert rc == promote_user_quizzes.EXIT_FAIL
    assert "PRECOMPUTE_HMAC_SECRET" in capsys.readouterr().err


def test_fails_on_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _set_required_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="kaboom")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        promote_user_quizzes.httpx,
        "AsyncClient",
        lambda *a, **k: real_client(*a, transport=transport, **k),
    )

    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(tmp_path / "x.json")]
    )
    assert rc == promote_user_quizzes.EXIT_FAIL
    assert "promotion-candidates returned 500" in capsys.readouterr().err


def test_writes_report_file_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_required_env(monkeypatch)
    _patch_async_client(
        monkeypatch, [_full_candidate(), _broken_candidate()]
    )

    out_path = tmp_path / "promoted.json"
    report_path = tmp_path / "report.json"
    rc = promote_user_quizzes.main(
        [
            "--api-url",
            "http://test",
            "--out",
            str(out_path),
            "--report-out",
            str(report_path),
        ]
    )
    assert rc == promote_user_quizzes.EXIT_OK
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["fetched"] == 2
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert report["written_path"] == str(out_path)
    assert any(
        f["slug"] == "broken-topic" for f in report["failures"]
    )
