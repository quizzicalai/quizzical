"""On-demand canonical-correctness evaluator (READ-ONLY).

Run this manually, as needed (despite cost), to audit whether the character /
outcome set chosen for a quiz topic is *correct*. It answers two questions:

1. CANONICAL topics (``canonical_for(topic)`` is non-empty): does the stored set
   MATCH the reviewed canonical set?  No LLM, no cost — a pure set comparison.
     * ``outcome_mode='single'`` (MBTI, Hogwarts, …): EXACT set match
       (order-independent, case/accent-folded).
     * ``outcome_mode='blended'`` (DISC, Big Five): PALETTE-consistent — every
       outcome must be a canonical dimension, but a blend (not exactly-one, not
       all-of-N) is allowed.
2. NON-canonical topics: an LLM JUDGE (the app's existing ``llm_service``, a
   cheap already-configured model — default ``gemini/gemini-flash-latest``)
   scores whether the set is correct + appropriate for the topic (1-10 + reason).
   Total LLM spend is bounded by ``--max-spend`` (a fail-safe cost ledger that
   mirrors the precompute cost-guard pattern): once the projected next call would
   exceed the cap, the remaining non-canonical topics are reported as ``skipped``
   rather than judged.

The evaluator NEVER mutates quizzes (no writes). It can scan STORED quizzes from
``session_history`` (``category`` + ``character_set`` JSONB) and/or take an
explicit list of ``(topic, character_set)`` pairs from a JSON file.

USAGE
-----
Scan stored quizzes (most recent 200, last 30 days), canonical checks only::

    python -m scripts.eval_canonical_correctness --since 30d --limit 200 --no-judge

Scan + LLM-judge non-canonical topics with a $2.00 cap on a cheap model::

    python -m scripts.eval_canonical_correctness \\
        --since 14d --limit 500 \\
        --judge-model gemini/gemini-flash-latest --max-spend 2.00

Evaluate an explicit list from a file (no DB needed)::

    python -m scripts.eval_canonical_correctness --input pairs.json --no-judge

    # pairs.json: [{"topic": "DISC", "character_set": ["Dominance", ...]}, ...]

Emit machine-readable JSON instead of the table::

    python -m scripts.eval_canonical_correctness --input pairs.json --json

DB connection is read from ``DATABASE_URL`` (or the app ``settings.DATABASE_URL``
fallback). For prod scans, export the prod DSN first. See the companion doc
``specifications/audit/CANONICAL-CORRECTNESS-EVALUATOR.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make ``scripts`` runnable as ``python -m scripts.eval_canonical_correctness``
# AND as a direct file path: ensure the backend root is importable.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.agent.canonical_sets import (  # noqa: E402
    canonical_for,
    canonical_outcome_mode,
    canonical_title_for,
)
from app.services.precompute.canonical_gate import (  # noqa: E402
    OUTCOME_MODE_BLENDED,
    compare_sets,
)

# ---------------------------------------------------------------------------
# Spend ledger (fail-safe cost cap — mirrors precompute cost-guard pattern)
# ---------------------------------------------------------------------------

# Conservative per-judge-call estimate (USD). A cheap flash judge prompt is
# small; we overshoot so the cap trips early rather than late.
COST_PER_JUDGE_CALL_USD: float = 0.002


@dataclass
class SpendLedger:
    """Cumulative spend tracker with a hard cap (USD). 0 disables enforcement."""

    cap_usd: float
    spent_usd: float = 0.0
    calls: int = 0

    def would_exceed(self, projected_usd: float) -> bool:
        if self.cap_usd <= 0:
            return False
        return (self.spent_usd + projected_usd) > self.cap_usd

    def charge(self, amount_usd: float) -> None:
        self.spent_usd = round(self.spent_usd + amount_usd, 6)
        self.calls += 1


# ---------------------------------------------------------------------------
# Records / report
# ---------------------------------------------------------------------------

VERDICT_CANON_OK = "canonical-correct"
VERDICT_CANON_MISMATCH = "canonical-mismatch"
VERDICT_JUDGE_GOOD = "non-canonical-good"
VERDICT_JUDGE_FLAGGED = "non-canonical-flagged"
VERDICT_SKIPPED = "skipped-budget"
VERDICT_JUDGE_UNAVAILABLE = "judge-unavailable"


@dataclass
class QuizRecord:
    topic: str
    names: list[str]
    session_id: str | None = None


@dataclass
class EvalRow:
    topic: str
    session_id: str | None
    is_canonical: bool
    outcome_mode: str | None
    canonical_title: str | None
    verdict: str
    score: int | None = None
    detail: str = ""


@dataclass
class Report:
    rows: list[EvalRow] = field(default_factory=list)
    spent_usd: float = 0.0
    cap_usd: float = 0.0
    judge_model: str | None = None

    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for r in self.rows:
            c[r.verdict] = c.get(r.verdict, 0) + 1
        return c


# ---------------------------------------------------------------------------
# Canonical check (no LLM, no cost)
# ---------------------------------------------------------------------------


def _check_canonical(record: QuizRecord) -> EvalRow:
    canon = canonical_for(record.topic) or []
    mode = canonical_outcome_mode(record.topic) or "single"
    title = canonical_title_for(record.topic)
    ok, diff = compare_sets(canon, record.names, outcome_mode=mode)
    if ok:
        if mode == OUTCOME_MODE_BLENDED:
            detail = f"palette-consistent ({len(record.names)}/{len(canon)} dims)"
        else:
            detail = f"exact match ({len(canon)} outcomes)"
    else:
        detail = diff
    return EvalRow(
        topic=record.topic,
        session_id=record.session_id,
        is_canonical=True,
        outcome_mode=mode,
        canonical_title=title,
        verdict=VERDICT_CANON_OK if ok else VERDICT_CANON_MISMATCH,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# LLM judge (non-canonical topics, cost-bounded)
# ---------------------------------------------------------------------------

DEFAULT_JUDGE_MODEL = "gemini/gemini-flash-latest"
JUDGE_PASS_SCORE = 7  # >= this is "good"; below is flagged for review

_JUDGE_SYSTEM_PROMPT = (
    "You are auditing a 'Which X are you?' personality quiz. You are given a "
    "TOPIC and the CHARACTER/OUTCOME SET the quiz uses. Decide whether that set "
    "is CORRECT and APPROPRIATE for the topic: the outcomes should be real, "
    "recognizable members/archetypes of the topic, mutually distinct, and "
    "complete enough to feel right (not missing an obvious member, no foreign "
    "entries from another franchise/domain). Score 1-10 (10 = perfect set) and "
    "give a one-sentence reason. Return STRICT JSON matching the schema."
)


def _build_judge_model():
    """Return the pydantic response model for the judge (imported lazily)."""
    from pydantic import BaseModel, Field  # noqa: PLC0415

    class _JudgeVerdict(BaseModel):
        score: int = Field(..., ge=1, le=10)
        reason: str = Field(default="", max_length=400)

    return _JudgeVerdict


async def _judge_non_canonical(
    record: QuizRecord,
    *,
    model: str,
    response_model: Any,
) -> EvalRow:
    """Score a non-canonical topic's set via the app's existing llm_service.

    Fail-safe: any LLM error yields a ``judge-unavailable`` verdict (never a
    silent pass), consistent with the precompute judge's fail-closed behaviour.
    """
    from app.services import llm_service  # noqa: PLC0415

    body = (
        f"TOPIC: {record.topic}\n"
        f"CHARACTER/OUTCOME SET ({len(record.names)}): "
        + ", ".join(record.names)
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": body},
    ]
    try:
        verdict = await llm_service.llm_service.get_structured_response(
            tool_name="canonical_correctness_judge",
            messages=messages,
            response_model=response_model,
            model=model,
            max_output_tokens=400,
            timeout_s=45,
            text_params={"temperature": 0.0},
            trace_id="canonical-correctness-eval",
        )
    except Exception as exc:  # noqa: BLE001 — fail-safe: never read as a pass
        return EvalRow(
            topic=record.topic,
            session_id=record.session_id,
            is_canonical=False,
            outcome_mode=None,
            canonical_title=None,
            verdict=VERDICT_JUDGE_UNAVAILABLE,
            detail=f"{type(exc).__name__}: {exc}"[:200],
        )

    score = int(verdict.score)
    return EvalRow(
        topic=record.topic,
        session_id=record.session_id,
        is_canonical=False,
        outcome_mode=None,
        canonical_title=None,
        verdict=VERDICT_JUDGE_GOOD if score >= JUDGE_PASS_SCORE else VERDICT_JUDGE_FLAGGED,
        score=score,
        detail=(verdict.reason or "").strip(),
    )


# ---------------------------------------------------------------------------
# Core evaluation loop
# ---------------------------------------------------------------------------


async def evaluate(
    records: list[QuizRecord],
    *,
    judge: bool,
    judge_model: str,
    max_spend_usd: float,
) -> Report:
    report = Report(cap_usd=max_spend_usd, judge_model=judge_model if judge else None)
    ledger = SpendLedger(cap_usd=max_spend_usd)
    response_model = _build_judge_model() if judge else None

    for rec in records:
        canon = canonical_for(rec.topic)
        if canon:
            report.rows.append(_check_canonical(rec))
            continue

        # Non-canonical: LLM judge (cost-bounded), or report as skipped.
        if not judge:
            report.rows.append(
                EvalRow(
                    topic=rec.topic, session_id=rec.session_id, is_canonical=False,
                    outcome_mode=None, canonical_title=None,
                    verdict=VERDICT_SKIPPED, detail="judge disabled (--no-judge)",
                )
            )
            continue
        if ledger.would_exceed(COST_PER_JUDGE_CALL_USD):
            report.rows.append(
                EvalRow(
                    topic=rec.topic, session_id=rec.session_id, is_canonical=False,
                    outcome_mode=None, canonical_title=None,
                    verdict=VERDICT_SKIPPED,
                    detail=f"max-spend ${max_spend_usd:.2f} reached",
                )
            )
            continue

        row = await _judge_non_canonical(
            rec, model=judge_model, response_model=response_model
        )
        # Charge only for a call that actually reached the backend.
        if row.verdict != VERDICT_JUDGE_UNAVAILABLE:
            ledger.charge(COST_PER_JUDGE_CALL_USD)
        report.rows.append(row)

    report.spent_usd = ledger.spent_usd
    return report


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------


def _names_from_character_set(raw: Any) -> list[str]:
    """Normalize a stored ``character_set`` (list of dicts or strings) → names."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for c in raw:
        if isinstance(c, str):
            name = c.strip()
        elif isinstance(c, dict):
            name = str(c.get("name") or c.get("display_name") or "").strip()
        else:
            name = ""
        if name:
            out.append(name)
    return out


def load_records_from_file(path: Path) -> list[QuizRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("--input JSON must be a list of {topic, character_set} objects")
    records: list[QuizRecord] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or item.get("category") or "").strip()
        if not topic:
            continue
        names = _names_from_character_set(item.get("character_set"))
        records.append(QuizRecord(topic=topic, names=names, session_id=item.get("session_id")))
    return records


_SINCE_RE = re.compile(r"^\s*(\d+)\s*([dhw])\s*$", re.IGNORECASE)


def _since_to_interval(since: str | None) -> str | None:
    """Convert ``30d`` / ``12h`` / ``2w`` → a Postgres interval string, or None."""
    if not since:
        return None
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must look like '30d', '12h', '2w' (got {since!r})")
    n, unit = int(m.group(1)), m.group(2).lower()
    unit_word = {"d": "days", "h": "hours", "w": "weeks"}[unit]
    return f"{n} {unit_word}"


def _normalize_dsn(raw: str) -> str:
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _resolve_dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        try:
            from app.core.config import settings  # noqa: PLC0415

            dsn = settings.DATABASE_URL
        except Exception:
            dsn = None
    if not dsn:
        raise SystemExit("No DATABASE_URL set; pass --input for an offline scan instead.")
    return _normalize_dsn(dsn)


async def load_records_from_db(*, since: str | None, limit: int) -> list[QuizRecord]:
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    dsn = _resolve_dsn()
    connect_args = {"ssl": True} if not dsn.startswith("sqlite") else {}
    engine = create_async_engine(dsn, connect_args=connect_args)
    interval = _since_to_interval(since)
    where = "WHERE created_at >= now() - (:interval)::interval" if interval else ""
    sql = (
        "SELECT session_id, category, character_set "
        "FROM session_history "
        f"{where} "
        "ORDER BY created_at DESC "
        "LIMIT :limit"
    )
    params: dict[str, Any] = {"limit": int(limit)}
    if interval:
        params["interval"] = interval
    records: list[QuizRecord] = []
    try:
        async with engine.connect() as conn:
            res = await conn.execute(text(sql), params)
            for row in res:
                session_id, category, char_set = row[0], row[1], row[2]
                if isinstance(char_set, str):
                    try:
                        char_set = json.loads(char_set)
                    except Exception:
                        char_set = []
                records.append(
                    QuizRecord(
                        topic=str(category or "").strip(),
                        names=_names_from_character_set(char_set),
                        session_id=str(session_id),
                    )
                )
    finally:
        await engine.dispose()
    return [r for r in records if r.topic]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_table(report: Report) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("CANONICAL CORRECTNESS EVALUATION")
    lines.append("=" * 78)
    counts = report.counts()
    lines.append(f"  canonical-correct : {counts.get(VERDICT_CANON_OK, 0)}")
    lines.append(f"  canonical-mismatch: {counts.get(VERDICT_CANON_MISMATCH, 0)}")
    lines.append(f"  non-canonical-good: {counts.get(VERDICT_JUDGE_GOOD, 0)}")
    lines.append(f"  non-canon-flagged : {counts.get(VERDICT_JUDGE_FLAGGED, 0)}")
    skipped = counts.get(VERDICT_SKIPPED, 0)
    unavail = counts.get(VERDICT_JUDGE_UNAVAILABLE, 0)
    if skipped:
        lines.append(f"  skipped (budget)  : {skipped}")
    if unavail:
        lines.append(f"  judge-unavailable : {unavail}")
    if report.judge_model:
        lines.append(
            f"  judge spend       : ${report.spent_usd:.4f} / cap ${report.cap_usd:.2f} "
            f"({report.judge_model})"
        )
    lines.append("-" * 78)
    lines.append(f"{'TOPIC':<28}{'VERDICT':<22}{'SCORE':<7}DETAIL")
    lines.append("-" * 78)
    for r in report.rows:
        score = str(r.score) if r.score is not None else "-"
        topic = (r.topic[:26] + "..") if len(r.topic) > 27 else r.topic
        detail = r.detail if len(r.detail) <= 30 else r.detail[:28] + ".."
        lines.append(f"{topic:<28}{r.verdict:<22}{score:<7}{detail}")
    lines.append("=" * 78)
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps(
        {
            "counts": report.counts(),
            "spent_usd": report.spent_usd,
            "cap_usd": report.cap_usd,
            "judge_model": report.judge_model,
            "rows": [
                {
                    "topic": r.topic,
                    "session_id": r.session_id,
                    "is_canonical": r.is_canonical,
                    "outcome_mode": r.outcome_mode,
                    "canonical_title": r.canonical_title,
                    "verdict": r.verdict,
                    "score": r.score,
                    "detail": r.detail,
                }
                for r in report.rows
            ],
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_canonical_correctness",
        description="On-demand READ-ONLY canonical-correctness evaluator.",
    )
    src = p.add_argument_group("sources")
    src.add_argument("--input", type=Path, help="JSON file of {topic, character_set} pairs.")
    src.add_argument("--since", help="Scan stored quizzes newer than e.g. 30d / 12h / 2w.")
    src.add_argument("--limit", type=int, default=200, help="Max stored quizzes to scan.")
    judge = p.add_argument_group("LLM judge (non-canonical topics)")
    judge.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                       help=f"Cheap, already-configured model (default {DEFAULT_JUDGE_MODEL}).")
    judge.add_argument("--no-judge", action="store_true",
                       help="Canonical checks only; report non-canonical as skipped.")
    judge.add_argument("--max-spend", type=float, default=1.0,
                       help="Hard USD cap on judge spend (fail-safe). 0 disables.")
    out = p.add_argument_group("output")
    out.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    return p


async def _amain(args: argparse.Namespace) -> int:
    if args.input:
        records = load_records_from_file(args.input)
    elif args.since is not None or args.limit:
        records = await load_records_from_db(since=args.since, limit=args.limit)
    else:
        print("Provide --input or --since/--limit for a stored scan.", file=sys.stderr)
        return 2

    report = await evaluate(
        records,
        judge=not args.no_judge,
        judge_model=args.judge_model,
        max_spend_usd=args.max_spend,
    )
    print(render_json(report) if args.json else render_table(report))
    # Non-zero exit when any canonical set is wrong (CI / nightly friendly).
    return 1 if report.counts().get(VERDICT_CANON_MISMATCH, 0) else 0


def main() -> None:
    args = build_arg_parser().parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
