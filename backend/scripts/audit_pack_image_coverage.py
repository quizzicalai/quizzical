"""Audit FAL image coverage across precompute starter pack source files.

For each ``starter_ranked_candidates_batch{N}.source.json`` under
``backend/configs/precompute/starter_packs/``, walks every topic's
``characters`` list and counts how many entries have a non-empty
``image_url``. Prints batch-level totals and any topics that are not fully
imaged so operators can target a backfill pass with
``scripts.backfill_images_for_batches``.

Usage (from ``backend/``):

    python -m scripts.audit_pack_image_coverage
    python -m scripts.audit_pack_image_coverage --json   # machine-readable
    python -m scripts.audit_pack_image_coverage --prod   # also probe prod DB

Exit code is always 0 — this is a reporting tool, not a CI gate. See
AC-PROD-R14-AUDIT-1.

``--prod`` requires ``PROD_DB_URL`` in the environment (typically populated
from ``az keyvault secret show --vault-name quizzical-shared-kv --name
database-url --query value -o tsv``). It queries the ``characters`` table
for every name in the archives and reports:

  - ``db_present``: characters with a non-null ``image_url`` in prod
  - ``match_archive``: characters whose prod URL matches the archive URL
  - ``drift``: present but with a different URL (typically caused by name
    collisions across packs — same name appears in multiple topics and is
    overwritten by the most recent seed)

Drift is informational, not a failure: the runtime cache-hit + HEAD-probe
in ``image_pipeline._url_alive`` always serves a working URL regardless
of which pack last seeded it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = REPO_ROOT / "backend" / "configs" / "precompute" / "starter_packs"


def _coverage(source_path: Path) -> dict:
    """Return ``{batch, with, total, topics: [(slug, with, total)...]}``."""
    data = json.loads(source_path.read_text(encoding="utf-8"))
    topics = data.get("topics") or data.get("packs") or data
    total = 0
    with_img = 0
    topic_rows: list[tuple[str, int, int]] = []
    if isinstance(topics, list):
        for t in topics:
            chars = t.get("characters") or t.get("character_set") or []
            if not isinstance(chars, list):
                continue
            tt = 0
            tw = 0
            for c in chars:
                tt += 1
                total += 1
                url = c.get("image_url")
                if isinstance(url, str) and url.strip():
                    tw += 1
                    with_img += 1
            if tt > 0:
                topic_rows.append((t.get("slug", "?"), tw, tt))
    return {
        "batch": source_path.name,
        "with": with_img,
        "total": total,
        "topics": topic_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="also probe prod characters table (requires PROD_DB_URL env var)",
    )
    args = parser.parse_args()

    sources = sorted(PACKS_DIR.glob("starter_ranked_candidates_batch*.source.json"))
    reports = [_coverage(p) for p in sources]

    prod_report: dict[str, dict[str, int]] | None = None
    if args.prod:
        if not os.environ.get("PROD_DB_URL"):
            print(
                "error: --prod requires PROD_DB_URL in env (export via "
                "`az keyvault secret show --vault-name quizzical-shared-kv "
                "--name database-url --query value -o tsv`)",
                file=sys.stderr,
            )
            return 2
        prod_report = asyncio.run(_probe_prod(sources))

    if args.json:
        out: dict = {"archives": reports}
        if prod_report is not None:
            out["prod"] = prod_report
        print(json.dumps(out, indent=2))
        return 0

    for r in reports:
        missing = [(s, w, t) for (s, w, t) in r["topics"] if w < t]
        line = f"{r['batch']}: {r['with']}/{r['total']}"
        if missing:
            mlist = ", ".join(f"{s}:{w}/{t}" for (s, w, t) in missing)
            line += f"  MISSING: {mlist}"
        print(line)

    grand_with = sum(r["with"] for r in reports)
    grand_total = sum(r["total"] for r in reports)
    pct = (grand_with / grand_total * 100.0) if grand_total else 0.0
    print(f"\nGRAND TOTAL: {grand_with}/{grand_total}  ({pct:.1f}% imaged)")

    if prod_report is not None:
        print("\n=== prod characters table (queried via PROD_DB_URL) ===")
        gt = sum(p["total"] for p in prod_report.values())
        gp = sum(p["db_present"] for p in prod_report.values())
        gm = sum(p["match_archive"] for p in prod_report.values())
        for name, p in prod_report.items():
            print(
                f"{name}: chars={p['total']} db_present={p['db_present']} "
                f"match_archive={p['match_archive']} drift={p['db_present']-p['match_archive']}"
            )
        print(
            f"\nPROD TOTAL: chars={gt} db_present={gp} "
            f"match_archive={gm} drift={gp-gm}"
        )
    return 0


def _normalize_dsn(raw: str) -> str:
    """asyncpg needs ssl via ``connect_args``; strip ``?sslmode=...`` and swap driver."""
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


async def _probe_prod(sources: list[Path]) -> dict[str, dict[str, int]]:
    """For each archive, compare character.image_url against the prod DB."""
    # Lazy-imported so the script remains usable without DB deps for --json/text only.
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    dsn = _normalize_dsn(os.environ["PROD_DB_URL"])
    engine = create_async_engine(dsn, connect_args={"ssl": True})

    result: dict[str, dict[str, int]] = {}
    try:
        for arc in sources:
            data = json.loads(arc.read_text(encoding="utf-8"))
            topics = data.get("topics") or data.get("packs") or data
            name_to_url: dict[str, str] = {}
            if isinstance(topics, list):
                for t in topics:
                    chars = t.get("characters") or t.get("character_set") or []
                    if not isinstance(chars, list):
                        continue
                    for c in chars:
                        n = c.get("name")
                        u = c.get("image_url")
                        if n and isinstance(u, str) and u.strip():
                            name_to_url[n] = u
            if not name_to_url:
                result[arc.name] = {"total": 0, "db_present": 0, "match_archive": 0}
                continue

            db_urls: dict[str, str | None] = {}
            async with engine.connect() as conn:
                # Per-row SELECT — ANY(:array) binding is unreliable for our setup.
                for n in name_to_url:
                    row = await conn.execute(
                        text("SELECT image_url FROM characters WHERE name = :n LIMIT 1"),
                        {"n": n},
                    )
                    r = row.first()
                    db_urls[n] = r[0] if r else None

            result[arc.name] = {
                "total": len(name_to_url),
                "db_present": sum(1 for u in db_urls.values() if u),
                "match_archive": sum(
                    1 for n, u in name_to_url.items() if db_urls.get(n) == u
                ),
            }
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    sys.exit(main())
