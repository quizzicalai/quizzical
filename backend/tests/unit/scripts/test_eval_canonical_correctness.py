"""Unit tests for the on-demand canonical-correctness evaluator.

Covers:
  - canonical-check logic (single exact + blended palette) — no LLM
  - the LLM-judge path with a FAKE llm_service (good vs flagged)
  - the --max-spend cap (fail-safe): non-canonical topics past the cap are
    reported as skipped, never silently passed
  - READ-ONLY input loaders
"""

from __future__ import annotations

import pytest

from scripts.eval_canonical_correctness import (
    COST_PER_JUDGE_CALL_USD,
    VERDICT_CANON_MISMATCH,
    VERDICT_CANON_OK,
    VERDICT_JUDGE_FLAGGED,
    VERDICT_JUDGE_GOOD,
    VERDICT_JUDGE_UNAVAILABLE,
    VERDICT_SKIPPED,
    QuizRecord,
    SpendLedger,
    evaluate,
)

pytestmark = pytest.mark.anyio


HOGWARTS = ["Gryffindor", "Slytherin", "Ravenclaw", "Hufflepuff"]
DISC = ["Dominance", "Influence", "Steadiness", "Conscientiousness"]


# ---------------------------------------------------------------------------
# Spend ledger
# ---------------------------------------------------------------------------


def test_spend_ledger_disabled_cap_never_exceeds() -> None:
    led = SpendLedger(cap_usd=0.0)
    assert led.would_exceed(1_000.0) is False


def test_spend_ledger_blocks_above_cap() -> None:
    led = SpendLedger(cap_usd=0.01)
    led.charge(0.008)
    assert led.would_exceed(0.005) is True  # 0.008 + 0.005 > 0.01
    assert led.would_exceed(0.001) is False


# ---------------------------------------------------------------------------
# Canonical-check logic (no LLM)
# ---------------------------------------------------------------------------


async def test_canonical_single_exact_match() -> None:
    recs = [QuizRecord(topic="Hogwarts Houses", names=HOGWARTS)]
    report = await evaluate(recs, judge=False, judge_model="x", max_spend_usd=0.0)
    assert report.rows[0].verdict == VERDICT_CANON_OK
    assert report.rows[0].outcome_mode == "single"


async def test_canonical_single_mismatch() -> None:
    recs = [QuizRecord(topic="Hogwarts Houses", names=["Gryffindor", "Slytherin"])]
    report = await evaluate(recs, judge=False, judge_model="x", max_spend_usd=0.0)
    assert report.rows[0].verdict == VERDICT_CANON_MISMATCH


async def test_canonical_blended_disc_partial_passes() -> None:
    recs = [QuizRecord(topic="DISC", names=["Dominance", "Influence"])]
    report = await evaluate(recs, judge=False, judge_model="x", max_spend_usd=0.0)
    assert report.rows[0].verdict == VERDICT_CANON_OK
    assert report.rows[0].outcome_mode == "blended"


async def test_no_judge_reports_non_canonical_as_skipped() -> None:
    recs = [QuizRecord(topic="Taylor Swift eras", names=["Lover", "Reputation"])]
    report = await evaluate(recs, judge=False, judge_model="x", max_spend_usd=0.0)
    assert report.rows[0].verdict == VERDICT_SKIPPED


# ---------------------------------------------------------------------------
# LLM-judge path with a FAKE llm_service
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, scores: list[int]) -> None:
        self._scores = iter(scores)
        self.calls = 0

    async def get_structured_response(self, *, response_model, **kwargs):
        self.calls += 1
        score = next(self._scores)
        return response_model(score=score, reason=f"score {score}")


@pytest.fixture
def fake_llm(monkeypatch):
    import app.services.llm_service as svc

    def _install(scores: list[int]) -> _FakeLLM:
        fake = _FakeLLM(scores)
        monkeypatch.setattr(svc, "llm_service", fake)
        return fake

    return _install


async def test_judge_good_and_flagged(fake_llm) -> None:
    fake = fake_llm([9, 3])  # first good, second flagged
    recs = [
        QuizRecord(topic="Taylor Swift eras", names=["Lover", "1989"]),
        QuizRecord(topic="Studio Ghibli films", names=["X", "Y"]),
    ]
    report = await evaluate(recs, judge=True, judge_model="gemini/gemini-flash-latest",
                            max_spend_usd=1.0)
    verdicts = [r.verdict for r in report.rows]
    assert verdicts == [VERDICT_JUDGE_GOOD, VERDICT_JUDGE_FLAGGED]
    assert report.rows[0].score == 9
    assert fake.calls == 2
    assert report.spent_usd == pytest.approx(2 * COST_PER_JUDGE_CALL_USD)


async def test_max_spend_cap_skips_remaining(fake_llm) -> None:
    fake = fake_llm([8, 8, 8])
    recs = [QuizRecord(topic=f"non-canon topic {i}", names=["A", "B"]) for i in range(3)]
    # Cap allows exactly ONE judge call.
    report = await evaluate(recs, judge=True, judge_model="m",
                            max_spend_usd=COST_PER_JUDGE_CALL_USD)
    verdicts = [r.verdict for r in report.rows]
    assert verdicts.count(VERDICT_JUDGE_GOOD) == 1
    assert verdicts.count(VERDICT_SKIPPED) == 2
    assert fake.calls == 1  # cap stopped further LLM calls


async def test_judge_failure_is_unavailable_not_a_pass(monkeypatch) -> None:
    import app.services.llm_service as svc

    class _Boom:
        async def get_structured_response(self, **kwargs):
            raise RuntimeError("llm down")

    monkeypatch.setattr(svc, "llm_service", _Boom())
    recs = [QuizRecord(topic="some non-canon topic", names=["A", "B"])]
    report = await evaluate(recs, judge=True, judge_model="m", max_spend_usd=1.0)
    assert report.rows[0].verdict == VERDICT_JUDGE_UNAVAILABLE
    # A judge outage must NOT be charged or read as good.
    assert report.spent_usd == 0.0


# ---------------------------------------------------------------------------
# Input loaders (READ-ONLY)
# ---------------------------------------------------------------------------


def test_load_records_from_file(tmp_path) -> None:
    import json

    from scripts.eval_canonical_correctness import load_records_from_file

    p = tmp_path / "pairs.json"
    p.write_text(
        json.dumps(
            [
                {"topic": "DISC", "character_set": [{"name": "Dominance"}, "Influence"]},
                {"topic": "", "character_set": []},  # skipped (no topic)
            ]
        ),
        encoding="utf-8",
    )
    recs = load_records_from_file(p)
    assert len(recs) == 1
    assert recs[0].topic == "DISC"
    assert recs[0].names == ["Dominance", "Influence"]
