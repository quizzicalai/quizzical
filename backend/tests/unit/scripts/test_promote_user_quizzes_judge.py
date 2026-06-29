"""Unit tests for the semantic/safety judge gate in
``scripts/promote_user_quizzes`` (audit P1).

These tests pin the behaviour the fix added: a structurally-valid topic is
ONLY promoted after the real two-judge LLM consensus
(:func:`app.services.precompute.evaluator.evaluate_single`) clears it. A
result with ``blocking_reasons`` (safety) or a sub-threshold score drops
the topic; a clean high-score result keeps it; ``--skip-judge`` bypasses
the judge entirely.

``evaluate_single`` is mocked so no LLM traffic is generated — the
structural pre-filter and the gating logic run for real.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest

from app.services.precompute.evaluator import EvaluatorResult
from scripts import promote_user_quizzes

SECRET = "promote-judge-secret-" + "x" * 48


def _full_candidate(slug: str = "brand-new-alpha") -> dict:
    """A structurally-valid candidate (passes the cheap pre-filter)."""
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
    """Fails the structural pre-filter: one character, one question."""
    base = _full_candidate(slug="broken-topic")
    base["characters"] = base["characters"][:1]
    base["baseline_questions"] = base["baseline_questions"][:1]
    return base


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch, candidates: list[dict]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == promote_user_quizzes.PROMOTION_CANDIDATES_PATH:
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


def _make_evaluate_single(result: EvaluatorResult, calls: dict):
    """Build a stub ``evaluate_single`` that records calls and returns
    ``result`` (or raises it, if an Exception instance is passed)."""

    async def _stub(*, judge_fn, artefact, tier, pass_score, require_two_judge, **kw):
        calls["n"] += 1
        calls["require_two_judge"] = require_two_judge
        calls["pass_score"] = pass_score
        calls["judge_fn"] = judge_fn
        if isinstance(result, BaseException):
            raise result
        return result

    return _stub


def _patch_evaluate_single(
    monkeypatch: pytest.MonkeyPatch, result, calls: dict
) -> None:
    # ``_evaluate`` imports ``evaluate_single`` from the evaluator module at
    # call time, so patching the attribute on that module is sufficient.
    import app.services.precompute.evaluator as evaluator_mod

    monkeypatch.setattr(
        evaluator_mod,
        "evaluate_single",
        _make_evaluate_single(result, calls),
    )


# ---------------------------------------------------------------------------
# Direct _evaluate unit coverage (no HTTP / signing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_drops_topic_with_blocking_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safety gate: any blocking reason drops the topic even with a high
    score."""
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=99, blocking_reasons=("unsafe_content",)),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_full_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(topics, pass_score=7)

    assert passed == []
    assert len(failed) == 1
    assert failed[0]["slug"] == "brand-new-alpha"
    assert failed[0]["stage"] == "judge"
    assert "unsafe_content" in failed[0]["blocking_reasons"]
    # The judge actually ran with two-judge consensus.
    assert calls["n"] == 1
    assert calls["require_two_judge"] is True


@pytest.mark.asyncio
async def test_evaluate_drops_topic_with_low_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quality gate: a sub-threshold score drops the topic."""
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=3, blocking_reasons=()),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_full_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(topics, pass_score=7)

    assert passed == []
    assert len(failed) == 1
    assert failed[0]["stage"] == "judge"
    assert failed[0]["judge_score"] == 3
    assert failed[0]["pass_score"] == 7


@pytest.mark.asyncio
async def test_evaluate_keeps_clean_high_score_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean, high-score result keeps the topic."""
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=95, blocking_reasons=()),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_full_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(topics, pass_score=7)

    assert failed == []
    assert len(passed) == 1
    assert passed[0]["slug"] == "brand-new-alpha"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_evaluate_structural_failure_never_reaches_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structurally-broken topic is dropped at the cheap pre-filter and
    the (expensive) judge is never invoked."""
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=95, blocking_reasons=()),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_broken_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(topics, pass_score=7)

    assert passed == []
    assert len(failed) == 1
    assert failed[0]["stage"] == "structural"
    assert calls["n"] == 0  # judge never ran


@pytest.mark.asyncio
async def test_evaluate_judge_escalation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A two-judge divergence (EscalateToTier3) cannot be re-judged offline,
    so the topic is dropped (fail closed)."""
    from app.services.precompute.evaluator import EscalateToTier3

    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EscalateToTier3((95, 50)),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_full_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(topics, pass_score=7)

    assert passed == []
    assert len(failed) == 1
    assert "two_judge_divergence" in failed[0]["blocking_reasons"]


@pytest.mark.asyncio
async def test_evaluate_judge_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected judge error MUST NOT promote unverified content."""
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        RuntimeError("backend down"),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_full_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(topics, pass_score=7)

    assert passed == []
    assert len(failed) == 1
    assert failed[0]["blocking_reasons"] == ["judge_error:RuntimeError"]


@pytest.mark.asyncio
async def test_evaluate_skip_judge_bypasses_judge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--skip-judge`` gates on the structural pre-filter only; the judge
    is never imported/invoked."""
    calls = {"n": 0}
    # Even if the judge would block, skip_judge must not call it.
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=0, blocking_reasons=("would_block",)),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_full_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(
        topics, skip_judge=True, pass_score=7
    )

    assert failed == []
    assert len(passed) == 1
    assert calls["n"] == 0  # judge never ran


@pytest.mark.asyncio
async def test_evaluate_skip_judge_still_drops_structural_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--skip-judge`` still honours the structural pre-filter."""
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=95, blocking_reasons=()),
        calls,
    )

    topics = [promote_user_quizzes._to_source_topic(_broken_candidate())]
    passed, failed = await promote_user_quizzes._evaluate(
        topics, skip_judge=True, pass_score=7
    )

    assert passed == []
    assert len(failed) == 1
    assert failed[0]["stage"] == "structural"
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# End-to-end coverage through main() (HTTP fetch + judge gate + sign)
# ---------------------------------------------------------------------------


def test_main_drops_unsafe_candidate_before_signing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A structurally-valid but judge-blocked candidate must NOT be signed
    into an archive — this is the P1 regression guard."""
    _set_required_env(monkeypatch)
    _patch_async_client(monkeypatch, [_full_candidate()])
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=99, blocking_reasons=("unsafe_content",)),
        calls,
    )

    out_path = tmp_path / "promoted.json"
    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(out_path)]
    )

    assert rc == promote_user_quizzes.EXIT_NO_CANDIDATES
    assert not out_path.exists()
    assert calls["n"] == 1  # the judge actually ran


def test_main_signs_clean_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean, high-score candidate is signed into the archive."""
    _set_required_env(monkeypatch)
    _patch_async_client(monkeypatch, [_full_candidate()])
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=95, blocking_reasons=()),
        calls,
    )

    out_path = tmp_path / "promoted.json"
    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(out_path)]
    )

    assert rc == promote_user_quizzes.EXIT_OK
    assert out_path.exists()
    assert calls["n"] == 1


def test_main_skip_judge_signs_without_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--skip-judge`` signs the structurally-valid candidate without ever
    invoking the judge."""
    _set_required_env(monkeypatch)
    _patch_async_client(monkeypatch, [_full_candidate()])
    calls = {"n": 0}
    _patch_evaluate_single(
        monkeypatch,
        EvaluatorResult(score=0, blocking_reasons=("would_block",)),
        calls,
    )

    out_path = tmp_path / "promoted.json"
    rc = promote_user_quizzes.main(
        ["--api-url", "http://test", "--out", str(out_path), "--skip-judge"]
    )

    assert rc == promote_user_quizzes.EXIT_OK
    assert out_path.exists()
    assert calls["n"] == 0  # judge bypassed
