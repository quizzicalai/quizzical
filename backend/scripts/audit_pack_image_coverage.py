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

Exit code is always 0 — this is a reporting tool, not a CI gate. See
AC-PROD-R14-AUDIT-1.
"""
from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args()

    sources = sorted(PACKS_DIR.glob("starter_ranked_candidates_batch*.source.json"))
    reports = [_coverage(p) for p in sources]

    if args.json:
        print(json.dumps(reports, indent=2))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
