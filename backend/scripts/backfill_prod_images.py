"""Backfill character images DIRECTLY from prod rosters (2026-07-02).

WHY
---
``backfill_images_for_batches`` only covers topics whose ``.source.json`` is
on disk; the 2026-07-02 audit found ~1616 imageless characters across 500
published packs in PROD (209 packs fully imageless, 291 partial), including
topics that were never staged on disk (or have drifted, 450 URL drift). This
script reads the rosters from the prod DB itself, generates with the FIXED
object-vs-person prompts (``image_tools.build_character_image_prompt``),
gates each image through the LLM concept judge, then IMMEDIATELY rehosts the
bytes into ``media_assets`` and rewrites ``characters.image_url`` to the
durable ``/api/v1/media/{id}`` URL (never leaves a fresh ephemeral FAL URL
behind).

Flow per character:
  1. build prompt (object-aware) -> FAL generate (seed = slug|name)
  2. LLM concept judge (gemini) -> drop on fail (never ship un-judged art)
  3. download bytes (SSRF-guarded) -> media_assets (sha256 dedup)
  4. UPDATE characters SET image_url = <api media url>, image_asset_id = ...
     + mirror session_history.character_set snapshots

SPEND SAFETY
------------
``--spend-cap-usd`` gates the run via the shared ``SpendLedger`` (FAL image
~$0.011 + judge ~$0.002 per character, conservative). The run STOPS at the
cap; progress is printed per topic. Resumable by design: characters that
gained an image are skipped on re-run.

USAGE (from backend/; PROD_DB_URL + FAL_KEY + GEMINI_API_KEY in env)
--------------------------------------------------------------------
    python -m scripts.backfill_prod_images --dry-run --limit 10
    python -m scripts.backfill_prod_images --limit 200 --spend-cap-usd 3.0
    # force-regen specific below-bar characters (vision-judge fails):
    python -m scripts.backfill_prod_images --names-file fails.json \
        --force-regen --spend-cap-usd 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any

from app.agent.tools import image_tools
from app.models.api import CharacterProfile
from app.services.image_service import _client_singleton as image_client
from scripts._precompute_spend import SpendLedger
from scripts.generate_images_for_packs import llm_image_judge
from scripts.rehost_fal_images import (
    MEDIA_PATH_FMT,
    _fetch_image,
    _rewrite_character,
    _upsert_media_asset,
)


def _normalize_dsn(raw: str) -> str:
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _style_suffix() -> str:
    from app.core.config import settings

    cfg = getattr(settings, "image_gen", None)
    return getattr(cfg, "style_suffix", "") if cfg else ""


def _negative_prompt() -> str:
    from app.core.config import settings

    cfg = getattr(settings, "image_gen", None)
    return getattr(cfg, "negative_prompt", "") if cfg else ""


async def _load_targets(
    engine: Any, *, names: list[str] | None, force_regen: bool, limit: int
) -> list[dict[str, Any]]:
    """Rows: {id, name, short_description, profile_text, image_url, topic}.

    Default: imageless characters referenced by the LATEST published pack of
    each topic, ordered so fully-imageless packs fill first (a pack going
    from 0->full art beats scattering singles). ``names`` restricts to an
    explicit list (and with ``force_regen`` also targets already-imaged rows,
    for below-bar regeneration).
    """
    from sqlalchemy import text

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    WITH latest AS (
                      SELECT DISTINCT ON (tp.topic_id) tp.*
                      FROM topic_packs tp WHERE tp.status='published'
                      ORDER BY tp.topic_id, tp.version DESC
                    )
                    SELECT t.slug, t.display_name, cs.composition
                    FROM latest l
                    JOIN topics t ON t.id = l.topic_id
                    JOIN character_sets cs ON cs.id = l.character_set_id
                    """
                )
            )
        ).mappings().all()
        chars = (
            await conn.execute(
                text(
                    "SELECT id::text AS id, name, short_description, "
                    "profile_text, image_url FROM characters"
                )
            )
        ).mappings().all()

    by_id = {c["id"]: c for c in chars}
    want = {n.strip() for n in (names or []) if n and n.strip()}
    seen: set[str] = set()

    def _topic_targets(r: Any) -> tuple[int, list[dict[str, Any]]]:
        """(n_imaged, targets) for one pack row."""
        comp = r["composition"]
        if isinstance(comp, str):
            comp = json.loads(comp)
        cids = [str(c) for c in (comp or {}).get("character_ids", [])]
        out: list[dict[str, Any]] = []
        n_imaged = 0
        for cid in cids:
            c = by_id.get(cid)
            if c is None:
                continue
            has_img = bool((c["image_url"] or "").strip())
            n_imaged += 1 if has_img else 0
            skip_by_names = want and (
                c["name"] not in want or (has_img and not force_regen)
            )
            skip_by_default = not want and has_img
            if cid in seen or skip_by_names or skip_by_default:
                continue
            seen.add(cid)
            out.append(
                {
                    "id": cid,
                    "name": c["name"],
                    "short_description": c["short_description"] or "",
                    "profile_text": c["profile_text"] or "",
                    "image_url": c["image_url"],
                    "topic": r["display_name"],
                    "slug": r["slug"],
                }
            )
        return n_imaged, out

    per_topic = []
    for r in rows:
        n_imaged, tt = _topic_targets(r)
        if tt:
            per_topic.append((n_imaged, -len(tt), tt))

    # Fully-imageless packs first (0 imaged), then most-missing first.
    per_topic.sort(key=lambda t: (t[0], t[1]))
    targets: list[dict[str, Any]] = []
    for _, _, tt in per_topic:
        targets.extend(tt)
    if limit > 0:
        targets = targets[:limit]
    return targets


async def run(
    *,
    db_url: str,
    public_base_url: str,
    names: list[str] | None,
    force_regen: bool,
    limit: int,
    spend_cap_usd: float,
    dry_run: bool,
) -> dict[str, Any]:
    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine

    ledger = SpendLedger(cap_cents=int(round(spend_cap_usd * 100)))
    engine = create_async_engine(_normalize_dsn(db_url), connect_args={"ssl": True})
    base = public_base_url.rstrip("/")
    stats = {
        "targets": 0, "generated": 0, "judge_failed": 0, "gen_failed": 0,
        "rehosted": 0, "rehost_failed": 0, "stopped_early": False,
    }
    style, neg = _style_suffix(), _negative_prompt()
    try:
        targets = await _load_targets(
            engine, names=names, force_regen=force_regen, limit=limit
        )
        stats["targets"] = len(targets)
        print(f"targets: {len(targets)}")
        if dry_run:
            for t in targets[:20]:
                profile = CharacterProfile(
                    name=t["name"],
                    short_description=t["short_description"] or "(none)",
                    profile_text=t["profile_text"] or "(none)",
                )
                spec = image_tools.build_character_image_prompt(
                    profile, category=t["topic"], analysis={},
                    style_suffix=style, negative_prompt=neg,
                )
                kind = image_tools.infer_subject_kind(
                    name=t["name"], category=t["topic"],
                    description=t["short_description"],
                )
                print(f"  [{kind:6s}] {t['name']} | {t['topic'][:38]} | "
                      f"{spec['prompt'][:90]}")
            return stats

        async with httpx.AsyncClient(follow_redirects=False) as client:

            async def _process_one(t: dict[str, Any]) -> None:
                profile = CharacterProfile(
                    name=t["name"],
                    short_description=t["short_description"] or "(none)",
                    profile_text=t["profile_text"] or "(none)",
                )
                spec = image_tools.build_character_image_prompt(
                    profile, category=t["topic"], analysis={},
                    style_suffix=style, negative_prompt=neg,
                )
                seed = image_tools.derive_seed(t["slug"], t["name"])
                fal_url = await image_client.generate(
                    prompt=spec["prompt"],
                    negative_prompt=spec["negative_prompt"],
                    seed=seed,
                    timeout_s=30,
                )
                ledger.charge_fal_image(1)
                if not fal_url:
                    stats["gen_failed"] += 1
                    return
                stats["generated"] += 1

                verdict = await llm_image_judge(
                    character_name=t["name"],
                    character_short_desc=t["short_description"],
                    character_profile=t["profile_text"],
                    category=t["topic"],
                    image_url=fal_url,
                    seed=seed,
                )
                ledger.charge_llm_judge(1)
                if not verdict.passed:
                    stats["judge_failed"] += 1
                    print(f"  JUDGE-FAIL {t['name']} ({t['topic'][:30]}): "
                          f"{verdict.blocking_reasons}")
                    return

                # Immediate rehost: bytes -> media_assets -> durable API URL.
                payload = await _fetch_image(fal_url, client)
                if payload is None:
                    stats["rehost_failed"] += 1
                    return
                data, ctype = payload
                from sqlalchemy import text as _sqltext

                async with engine.begin() as conn:
                    # The character row may hold an old URL (force-regen) or
                    # NULL; write the FAL url first so the guarded rewrite
                    # (WHERE image_url = :old_url) matches deterministically.
                    await conn.execute(
                        _sqltext(
                            "UPDATE characters SET image_url = :u, "
                            "last_updated_at = now() WHERE id = :cid"
                        ),
                        {"u": fal_url, "cid": t["id"]},
                    )
                    asset_id = await _upsert_media_asset(
                        conn, data=data, content_type=ctype, source_url=fal_url
                    )
                    new_url = base + MEDIA_PATH_FMT.format(asset_id=asset_id)
                    await _rewrite_character(
                        conn, character_id=t["id"], name=t["name"],
                        old_url=fal_url, asset_id=asset_id, new_url=new_url,
                    )
                stats["rehosted"] += 1

            # Chunked fan-out: FAL calls are bounded by the client's own
            # semaphore (image_gen.concurrency); the ledger cap is checked at
            # chunk boundaries (max overshoot = one chunk's projected cost).
            chunk_size = 12
            for start in range(0, len(targets), chunk_size):
                if ledger.would_exceed(2.0):
                    print(f"== spend cap reached at {start}/{len(targets)} "
                          f"(${ledger.spent_usd:.2f})")
                    stats["stopped_early"] = True
                    break
                chunk = targets[start : start + chunk_size]
                await asyncio.gather(*[_process_one(t) for t in chunk])
                print(f"progress: {min(start + chunk_size, len(targets))}/"
                      f"{len(targets)} spent=${ledger.spent_usd:.2f}", flush=True)
    finally:
        await engine.dispose()

    stats["spend_usd"] = ledger.spent_usd
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=0, help="max characters (0=all)")
    p.add_argument("--spend-cap-usd", type=float, default=5.0,
                   help="hard spend cap for this run (default 5.0)")
    p.add_argument("--dry-run", action="store_true",
                   help="list targets + prompts; no FAL, no writes")
    p.add_argument("--names-file", type=argparse.FileType("r", encoding="utf-8"),
                   default=None,
                   help="JSON list of character names to (re)generate")
    p.add_argument("--force-regen", action="store_true",
                   help="with --names-file: regenerate even if already imaged")
    p.add_argument("--public-base-url",
                   default=os.environ.get(
                       "API_PUBLIC_BASE_URL",
                       "https://api-quizzical-dev.whitesea-815b33ea.westus2"
                       ".azurecontainerapps.io",
                   ))
    args = p.parse_args(argv)

    db_url = os.environ.get("PROD_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("error: set PROD_DB_URL (or DATABASE_URL)", file=sys.stderr)
        return 2

    names = None
    if args.names_file:
        loaded = json.load(args.names_file)
        names = [str(x) for x in loaded] if isinstance(loaded, list) else None

    stats = asyncio.run(
        run(
            db_url=db_url,
            public_base_url=args.public_base_url,
            names=names,
            force_regen=args.force_regen,
            limit=args.limit,
            spend_cap_usd=args.spend_cap_usd,
            dry_run=args.dry_run,
        )
    )
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
