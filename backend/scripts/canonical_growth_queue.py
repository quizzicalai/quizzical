"""Canonical growth queue — surface POPULAR non-canonical topics (READ-ONLY).

The owner's goal is "our canonical set only improves the more we add to it".
This lightweight report scans ``session_history`` for the most-frequent
``category`` values that have NO ``canonical_for`` match, so the owner can curate
+ add them to the reviewed code catalog. It is the on-ramp for catalog growth.

It is READ-ONLY (never mutates quizzes). It groups by a noise-normalized topic
key (so "What is my X" and "X quiz" count together) and ranks by frequency.

USAGE
-----
Top 30 popular non-canonical topics over the last 90 days::

    python -m scripts.canonical_growth_queue --since 90d --top 30

Emit JSON (e.g. to append to the living backlog doc)::

    python -m scripts.canonical_growth_queue --since 90d --top 50 --json

Offline (no DB) from a JSON list of {category} rows::

    python -m scripts.canonical_growth_queue --input rows.json --top 30

DB DSN comes from ``DATABASE_URL`` (or app ``settings.DATABASE_URL``). The living
backlog doc is ``specifications/audit/CANONICAL-COVERAGE-2026-06-30.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.agent.canonical_sets import canonical_for  # noqa: E402

# Reuse the canonical noise-stripping normalizer so grouping mirrors the lookup's
# view of a topic: "What are the greek gods", "greek gods quiz" and "Greek gods"
# all fold to the same growth-queue bucket.
try:
    from app.agent.canonical_sets import (
        _norm_key as _norm_topic,  # type: ignore  # noqa: E402
    )
except Exception:  # pragma: no cover - defensive fallback
    def _norm_topic(s: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


@dataclass
class QueueEntry:
    topic_key: str
    sample_label: str  # a representative raw category for display
    count: int


def build_growth_queue(categories: list[str], *, top: int) -> list[QueueEntry]:
    """Rank non-canonical topics by frequency (grouped by normalized key)."""
    counter: Counter[str] = Counter()
    label_by_key: dict[str, str] = {}
    for raw in categories:
        raw = (raw or "").strip()
        if not raw:
            continue
        # Skip anything already canonical — those are "done".
        if canonical_for(raw):
            continue
        key = _norm_topic(raw) or raw.lower()
        counter[key] += 1
        # Keep the shortest representative label (usually the cleanest phrasing).
        if key not in label_by_key or len(raw) < len(label_by_key[key]):
            label_by_key[key] = raw
    ranked = counter.most_common(top if top and top > 0 else None)
    return [
        QueueEntry(topic_key=k, sample_label=label_by_key.get(k, k), count=n)
        for k, n in ranked
    ]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

_SINCE_RE = re.compile(r"^\s*(\d+)\s*([dhw])\s*$", re.IGNORECASE)


def _since_to_interval(since: str | None) -> str | None:
    if not since:
        return None
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must look like '90d', '12h', '2w' (got {since!r})")
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
        raise SystemExit("No DATABASE_URL set; pass --input for an offline run instead.")
    return _normalize_dsn(dsn)


async def load_categories_from_db(*, since: str | None, limit: int) -> list[str]:
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    dsn = _resolve_dsn()
    connect_args = {"ssl": True} if not dsn.startswith("sqlite") else {}
    engine = create_async_engine(dsn, connect_args=connect_args)
    interval = _since_to_interval(since)
    where = "WHERE created_at >= now() - (:interval)::interval" if interval else ""
    sql = f"SELECT category FROM session_history {where} ORDER BY created_at DESC LIMIT :limit"
    params: dict[str, Any] = {"limit": int(limit)}
    if interval:
        params["interval"] = interval
    out: list[str] = []
    try:
        async with engine.connect() as conn:
            res = await conn.execute(text(sql), params)
            out = [str(r[0]) for r in res if r[0]]
    finally:
        await engine.dispose()
    return out


def load_categories_from_file(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            c = item.get("category") or item.get("topic")
            if c:
                out.append(str(c))
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_table(entries: list[QueueEntry]) -> str:
    lines = ["CANONICAL GROWTH QUEUE (popular non-canonical topics)", "-" * 60,
             f"{'COUNT':<8}TOPIC"]
    for e in entries:
        lines.append(f"{e.count:<8}{e.sample_label}")
    if not entries:
        lines.append("(no non-canonical topics found)")
    return "\n".join(lines)


def render_markdown(entries: list[QueueEntry]) -> str:
    lines = ["| Count | Topic (representative) | Normalized key |", "|---|---|---|"]
    for e in entries:
        lines.append(f"| {e.count} | {e.sample_label} | `{e.topic_key}` |")
    return "\n".join(lines)


def render_json(entries: list[QueueEntry]) -> str:
    return json.dumps(
        [{"count": e.count, "topic": e.sample_label, "key": e.topic_key} for e in entries],
        indent=2,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="canonical_growth_queue",
        description="Surface popular non-canonical topics as a canonical growth queue.",
    )
    p.add_argument("--input", type=Path, help="Offline JSON list of categories.")
    p.add_argument("--since", help="Only quizzes newer than e.g. 90d / 12h / 2w.")
    p.add_argument("--limit", type=int, default=5000, help="Max rows to scan.")
    p.add_argument("--top", type=int, default=30, help="How many topics to surface.")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="Emit JSON.")
    fmt.add_argument("--markdown", action="store_true", help="Emit a Markdown table.")
    return p


async def _amain(args: argparse.Namespace) -> int:
    if args.input:
        categories = load_categories_from_file(args.input)
    else:
        categories = await load_categories_from_db(since=args.since, limit=args.limit)
    entries = build_growth_queue(categories, top=args.top)
    if args.json:
        print(render_json(entries))
    elif args.markdown:
        print(render_markdown(entries))
    else:
        print(render_table(entries))
    return 0


def main() -> None:
    args = build_arg_parser().parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
