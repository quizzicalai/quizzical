"""Nightly promotion: user-quiz completions → signed starter-pack archive.

The pipeline closes the loop on user-driven content discovery: completed
quiz sessions that pass the LLM judge (and weren't down-voted by the user)
get packaged into a signed archive that the seeder workflow can ingest
into production. This is the same on-disk shape used by the orchestrator
in `scripts.precompute_and_deploy_in_batches`, so the seeded content
joins the existing canonical pool with no schema drift.

Steps (per nightly run):

  1. Fetch promotion candidates from the deployed API
     (``GET /admin/precompute/promotion-candidates`` — operator-only).
  2. Re-evaluate each candidate via
     :func:`scripts.generate_ranked_pack_candidates.evaluate_topic_entry`.
  3. Build a signed archive from the passing entries via
     :func:`scripts.build_starter_packs.build_archive`.
  4. Write the archive + sig + source + report to
     ``backend/configs/precompute/promoted_packs/promoted_<YYYYMMDD>.json``.
  5. (Outside this script) GitHub Actions commits the new files and
     dispatches the existing ``Seed prod precompute packs`` workflow,
     which runs the post-seed render smoke as its verification gate.

Idempotency: the importer dedupes on ``(topic_id, version)`` and on
canonical character keys, so re-running a day's archive against prod is
safe — already-seeded slugs are simply skipped.

Usage::

    python -m scripts.promote_user_quizzes \\
        --api-url https://api-quizzical-dev.... \\
        --token-env OPERATOR_TOKEN \\
        --out backend/configs/precompute/promoted_packs/promoted_20260101.json \\
        --secret-env PRECOMPUTE_HMAC_SECRET

Exit codes::

    0 — wrote a non-empty archive
    2 — no eligible candidates after evaluation (writes nothing,
        useful exit signal so the workflow skips the commit step)
    1 — any other failure (HTTP error, sign failure, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = (
    "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"
)
PROMOTION_CANDIDATES_PATH = "/api/v1/admin/precompute/promotion-candidates"
TIMEOUT_S = 30.0
MIN_SECRET_LEN = 32

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NO_CANDIDATES = 2


@dataclass
class PromotionReport:
    fetched: int
    evaluated: int
    passed: int
    failed: int
    written_path: str | None
    failures: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "evaluated": self.evaluated,
            "passed": self.passed,
            "failed": self.failed,
            "written_path": self.written_path,
            "failures": self.failures,
        }


async def _fetch_candidates(
    *,
    api_url: str,
    token: str,
    since_hours: int,
    limit: int,
    min_judge_score: int,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=api_url, timeout=TIMEOUT_S) as client:
        resp = await client.get(
            PROMOTION_CANDIDATES_PATH,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "since_hours": since_hours,
                "limit": limit,
                "min_judge_score": min_judge_score,
                "require_baseline_questions": "true",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"promotion-candidates returned {resp.status_code}: {resp.text[:300]}"
        )
    body = resp.json() or {}
    return list(body.get("candidates") or [])


def _to_source_topic(candidate: dict[str, Any]) -> dict[str, Any]:
    """Convert the API candidate shape into the source-document shape that
    `scripts.build_starter_packs.build_archive` consumes."""
    return {
        "slug": candidate["slug"],
        "display_name": candidate["display_name"],
        "aliases": [candidate["category"]],
        "synopsis": candidate["synopsis"],
        "characters": candidate["characters"],
        "baseline_questions": candidate["baseline_questions"],
    }


def _evaluate(topics: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the existing offline evaluator on each topic. Returns
    ``(passed, failed_with_reasons)``."""
    from scripts.generate_ranked_pack_candidates import evaluate_topic_entry

    passed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for topic in topics:
        out = evaluate_topic_entry(topic)
        if out.get("ready"):
            passed.append(topic)
        else:
            failed.append(
                {
                    "slug": topic.get("slug"),
                    "errors": out.get("errors", []),
                    "score": out.get("score", 0),
                }
            )
    return passed, failed


def _build_and_sign(
    *, topics: list[dict[str, Any]], out_path: Path, secret: str
) -> None:
    from scripts.build_starter_packs import _canonical_json, build_archive
    from scripts.import_packs import sign_archive

    source_doc = {
        "version": 3,
        "built_in_env": "promoted",
        "topics": topics,
    }
    archive = build_archive(source_doc)
    payload = _canonical_json(archive)
    signature = sign_archive(payload, secret=secret)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    sig_path = out_path.with_suffix(out_path.suffix + ".sig")
    sig_path.write_text(signature, encoding="utf-8")

    # Also write the source doc for auditability — same convention as
    # `precompute_and_deploy_in_batches`.
    src_path = out_path.with_name(out_path.stem + ".source.json")
    src_path.write_text(
        json.dumps(source_doc, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    token = (os.getenv(args.token_env) or "").strip()
    if not token:
        print(
            f"ERROR: env var {args.token_env} is empty",
            file=sys.stderr,
        )
        return EXIT_FAIL

    secret = (os.getenv(args.secret_env) or "").strip()
    if not secret or len(secret) < MIN_SECRET_LEN:
        print(
            f"ERROR: env var {args.secret_env} missing or shorter than {MIN_SECRET_LEN} chars",
            file=sys.stderr,
        )
        return EXIT_FAIL

    try:
        candidates = await _fetch_candidates(
            api_url=args.api_url,
            token=token,
            since_hours=args.since_hours,
            limit=args.limit,
            min_judge_score=args.min_judge_score,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"ERROR: failed to fetch candidates: {exc!r}", file=sys.stderr)
        return EXIT_FAIL

    topics = [_to_source_topic(c) for c in candidates]
    passed, failed = _evaluate(topics)

    report = PromotionReport(
        fetched=len(candidates),
        evaluated=len(topics),
        passed=len(passed),
        failed=len(failed),
        written_path=None,
        failures=failed,
    )

    out_path = Path(args.out).resolve()
    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(
            json.dumps(report.to_dict(), indent=2),
            encoding="utf-8",
        )

    if not passed:
        print(json.dumps(report.to_dict()))
        print(
            "INFO: no candidates passed evaluation — writing nothing",
            file=sys.stderr,
        )
        return EXIT_NO_CANDIDATES

    try:
        _build_and_sign(topics=passed, out_path=out_path, secret=secret)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: build/sign failed: {exc!r}", file=sys.stderr)
        return EXIT_FAIL

    report.written_path = str(out_path)
    if args.report_out:
        Path(args.report_out).write_text(
            json.dumps(report.to_dict(), indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report.to_dict()))
    return EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    p.add_argument(
        "--token-env",
        default="OPERATOR_TOKEN",
        help="Env var holding the operator bearer token.",
    )
    p.add_argument(
        "--secret-env",
        default="PRECOMPUTE_HMAC_SECRET",
        help="Env var holding the HMAC secret used to sign the archive.",
    )
    p.add_argument(
        "--out",
        required=True,
        help="Path for the signed archive .json. A .sig and .source.json are written alongside.",
    )
    p.add_argument(
        "--report-out",
        default=None,
        help="Optional path for a JSON report (counts + failure reasons).",
    )
    p.add_argument("--since-hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--min-judge-score", type=int, default=7)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
