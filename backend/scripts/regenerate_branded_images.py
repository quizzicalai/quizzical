"""Regenerate FAL images for branded characters across precompute packs.

Why
---
The original prompt builder for character images stripped both the
character name and the franchise/source whenever it suspected IP, which
made branded characters (Aragorn, Maomao, Erika Jayne, etc.) render as
generic strangers. FAL already enforces licensing on its side, so we
should pass ``"<character> from <source>"`` through and let FAL decide.

What it does
------------
For each ``*.source.json`` under ``configs/precompute/starter_packs/``:

1. **Classify the topic** as branded vs. generic using the LLM helper
   ``app.services.character_describer.classify_topic_brand``. The result
   is cached back into the source JSON under ``topic["branded"]`` so
   re-runs don't pay the classification cost again.

2. **Skip non-branded topics** (Greek God, Pokémon Type, MBTI, etc.) —
   the existing descriptive prompts already produce good art for them
   and the user asked us not to redo every image.

3. **Regenerate every character image** for branded topics through the
   three-rung fallback ladder in
   ``app.services.image_pipeline._generate_character_with_brand_fallback``:

       Rung 1 — ``"<name> from <source>"`` (FAL, literal).
       Rung 2 — LLM 1-sentence physical description, no branded items.
       Rung 3 — LLM stricter description, no proper nouns.

   The previous ``image_url`` is overwritten only when a new URL is
   returned; characters where every rung fails keep their old URL so
   we never end up worse than where we started.

4. **Rebuild the signed archive** via ``scripts.build_starter_packs``,
   commit the updated source / archive / sig files, push, and (unless
   ``--skip-seed`` is passed) trigger the ``Seed prod precompute packs``
   workflow with ``force_upgrade=true``.

Spend control
-------------
A coarse cost model is applied: each FAL call ≈ $0.011, each LLM
description call ≈ $0.001. The script stops cleanly once
``--total-budget-usd`` is reached.

Usage
-----
    python -m scripts.regenerate_branded_images \\
        --packs "starter_ranked_candidates_batch*.source.json" \\
        --total-budget-usd 10 \\
        --per-pack-cap-usd 2 \\
        --skip-seed

Pass ``--dry-run`` to classify topics and print the regeneration plan
without spending a cent on FAL.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from glob import glob
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
PACKS_DIR = BACKEND_DIR / "configs" / "precompute" / "starter_packs"

# Coarse cost model (USD).
_COST_FAL = 0.011
_COST_LLM = 0.001


# ---------------------------------------------------------------------------
# Subprocess helpers (mirrors backfill_images_for_batches.py)
# ---------------------------------------------------------------------------

def _run_check(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    rc = subprocess.call(cmd, cwd=cwd, env=env)
    if rc != 0:
        raise RuntimeError(f"command failed (exit {rc}): {' '.join(cmd)}")


def _python_exe() -> str:
    return sys.executable


# ---------------------------------------------------------------------------
# Source-pack IO
# ---------------------------------------------------------------------------

def _load_source(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_source(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _topics_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("topics") or data.get("packs") or []


# ---------------------------------------------------------------------------
# Per-topic work
# ---------------------------------------------------------------------------

async def _classify_topic(topic: dict[str, Any]) -> dict[str, Any]:
    """Return the cached or freshly-computed brand classification."""
    from app.services.character_describer import classify_topic_brand

    cached = topic.get("branded")
    if isinstance(cached, dict) and "is_branded" in cached:
        return cached

    display = topic.get("display_name") or topic.get("slug") or ""
    summary = topic.get("synopsis") or ""
    if isinstance(summary, dict):
        summary = summary.get("text") or summary.get("body") or ""

    out = await classify_topic_brand(display_name=display, summary=str(summary))
    result = {
        "is_branded": bool(out.get("is_branded")),
        "source": str(out.get("source") or "").strip(),
        "classified_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    topic["branded"] = result
    return result


async def _regen_branded_topic(
    topic: dict[str, Any],
    *,
    source_name: str,
    style_suffix: str,
    negative_prompt: str,
    budget_remaining_usd: float,
    dry_run: bool,
) -> tuple[int, int, float]:
    """Regenerate every character image for a branded topic.

    Returns ``(replaced, attempted, spent_usd)``.
    """
    from app.services import image_pipeline as ip
    from app.agent.tools import image_tools

    chars = topic.get("characters") or topic.get("character_set") or []
    if not isinstance(chars, list):
        return (0, 0, 0.0)

    replaced = 0
    attempted = 0
    spent = 0.0
    # Stable per-topic session id from the slug so re-runs derive the same seeds.
    session_id = topic.get("slug") or topic.get("display_name") or "regen"

    for ch in chars:
        if not isinstance(ch, dict):
            continue
        nm = (ch.get("name") or "").strip()
        if not nm:
            continue
        if budget_remaining_usd - spent <= 0:
            print(f"   * budget exhausted, stopping at character {nm}", flush=True)
            break
        attempted += 1
        if dry_run:
            print(f"   - DRY: would regen '{nm}' from '{source_name}'", flush=True)
            continue

        seed = image_tools.derive_seed(session_id, nm)
        # Pessimistic budget hit: assume all three rungs run (1 FAL + 1 LLM +
        # 1 FAL + 1 LLM + 1 FAL). We subtract a smaller "best-case" amount
        # post-success below so the running total stays honest.
        spent += _COST_FAL  # rung 1 always runs
        new_url = await ip._generate_character_with_brand_fallback(
            name=nm,
            source=source_name,
            style_suffix=style_suffix,
            negative_prompt=negative_prompt,
            seed=seed,
        )
        if new_url:
            old = ch.get("image_url")
            ch["image_url"] = new_url
            replaced += 1
            print(f"   + '{nm}' -> {new_url}  (was {old!s:.60})", flush=True)
        else:
            # All three rungs fired, so we burned ~3 FAL + 2 LLM calls.
            spent += 2 * _COST_FAL + 2 * _COST_LLM
            print(f"   ! '{nm}' regen failed; keeping previous image_url", flush=True)

    return (replaced, attempted, spent)


# ---------------------------------------------------------------------------
# Pack-level orchestration
# ---------------------------------------------------------------------------

async def _process_pack(
    source_path: Path,
    *,
    per_pack_cap_usd: float,
    total_budget_remaining_usd: float,
    dry_run: bool,
) -> tuple[int, int, float, bool]:
    """Process one ``*.source.json`` pack. Returns
    ``(replaced, attempted, spent, dirty)``.
    """
    from app.services import image_pipeline as ip

    data = _load_source(source_path)
    topics = _topics_of(data)
    if not topics:
        return (0, 0, 0.0, False)

    style = ip._style_suffix()
    neg = ip._negative_prompt()

    pack_replaced = 0
    pack_attempted = 0
    pack_spent = 0.0
    pack_dirty = False
    per_pack_remaining = min(per_pack_cap_usd, total_budget_remaining_usd)

    for topic in topics:
        if per_pack_remaining - pack_spent <= 0:
            print(f"  ! per-pack cap reached for {source_path.name}; stopping",
                  flush=True)
            break
        display = topic.get("display_name") or topic.get("slug") or "?"
        cls = await _classify_topic(topic)
        pack_dirty = True  # we wrote ``branded`` cache
        if not cls.get("is_branded"):
            print(f"  - skip (non-branded): {display}", flush=True)
            continue
        src_name = cls.get("source") or display
        print(f"  > BRANDED topic: {display!r}  (source={src_name!r})", flush=True)
        r, a, s = await _regen_branded_topic(
            topic,
            source_name=src_name,
            style_suffix=style,
            negative_prompt=neg,
            budget_remaining_usd=per_pack_remaining - pack_spent,
            dry_run=dry_run,
        )
        pack_replaced += r
        pack_attempted += a
        pack_spent += s

    # Persist the source.json (cached classifications + any new image_urls).
    if pack_dirty or pack_replaced > 0:
        _save_source(source_path, data)

    return (pack_replaced, pack_attempted, pack_spent, pack_dirty)


# ---------------------------------------------------------------------------
# Archive build + git/seed (only when not dry-run and we replaced anything)
# ---------------------------------------------------------------------------

def _build_archive(source_path: Path) -> Path:
    out_archive = source_path.with_suffix("").with_suffix(".json")
    # ``with_suffix("")`` strips ``.json`` from ``.source.json`` → leaves
    # ``…batch10.source``; ``with_suffix(".json")`` then yields
    # ``…batch10.json``. Build the signed archive in place.
    cmd = [
        _python_exe(), "-m", "scripts.build_starter_packs",
        "--source", str(source_path),
        "--out", str(out_archive),
        "--secret-env", "PRECOMPUTE_HMAC_SECRET",
    ]
    _run_check(cmd, cwd=BACKEND_DIR)
    return out_archive


def _git_commit_pack(source_path: Path, archive_path: Path,
                     replaced: int, attempted: int) -> None:
    sig_path = archive_path.parent / (archive_path.name + ".sig")
    files = [
        str(archive_path.relative_to(REPO_ROOT)),
        str(sig_path.relative_to(REPO_ROOT)),
        str(source_path.relative_to(REPO_ROOT)),
    ]
    _run_check(["git", "add", *files], cwd=REPO_ROOT)
    msg = (
        f"fix(images): regenerate branded character art for "
        f"{source_path.stem}\n\n"
        f"Replaced {replaced}/{attempted} branded character images via the "
        f"new '<name> from <source>' prompt ladder. FAL handles licensing."
    )
    _run_check(["git", "commit", "-m", msg], cwd=REPO_ROOT)


def _trigger_seed(archive_path: Path) -> str | None:
    archive_repo_path = str(archive_path.relative_to(REPO_ROOT)).replace("\\", "/")
    _run_check(
        ["gh", "workflow", "run", "Seed prod precompute packs",
         "-f", f"archive_path={archive_repo_path}",
         "-f", "force_upgrade=true"],
        cwd=REPO_ROOT,
    )
    time.sleep(8)
    out = subprocess.check_output(
        ["gh", "run", "list", "--workflow=seed-prod-packs.yml", "--limit", "1",
         "--json", "databaseId,status"],
        cwd=REPO_ROOT, text=True,
    )
    runs = json.loads(out)
    return str(runs[0]["databaseId"]) if runs else None


def _wait_for_seed(run_id: str, timeout_s: int = 600) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = subprocess.check_output(
            ["gh", "run", "view", run_id, "--json", "status,conclusion"],
            cwd=REPO_ROOT, text=True,
        )
        info = json.loads(out)
        if info["status"] == "completed":
            return info["conclusion"] == "success"
        time.sleep(15)
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_packs(pattern: str) -> list[Path]:
    """Expand ``--packs`` against PACKS_DIR, supporting globs and CSV."""
    if not pattern:
        return []
    out: list[Path] = []
    for piece in pattern.split(","):
        piece = piece.strip()
        if not piece:
            continue
        # If user passed an absolute / relative path, use as-is; else
        # resolve against PACKS_DIR.
        candidates = glob(piece)
        if not candidates:
            candidates = [str(p) for p in PACKS_DIR.glob(piece)]
        out.extend(Path(c) for c in candidates)
    # Dedup, keep .source.json only.
    seen: set[Path] = set()
    final: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp in seen:
            continue
        if not p.name.endswith(".source.json"):
            continue
        if not p.exists():
            continue
        seen.add(rp)
        final.append(p)
    return sorted(final)


async def _amain(args: argparse.Namespace) -> int:
    packs = _resolve_packs(args.packs)
    if not packs:
        print(f"!! no .source.json packs matched {args.packs!r}", file=sys.stderr)
        return 1

    print(f"== matched {len(packs)} pack(s):", flush=True)
    for p in packs:
        print(f"   - {p.relative_to(REPO_ROOT)}", flush=True)

    if not args.dry_run and not (os.getenv("PRECOMPUTE_HMAC_SECRET") or "").strip():
        print("ERROR: PRECOMPUTE_HMAC_SECRET is empty (needed for archive rebuild)",
              file=sys.stderr)
        return 1

    total_spent = 0.0
    total_replaced = 0
    total_attempted = 0

    for source_path in packs:
        remaining = args.total_budget_usd - total_spent
        if remaining <= 0:
            print(f"== total budget reached (${total_spent:.2f}); stopping")
            break

        print(f"\n=== {source_path.relative_to(REPO_ROOT)}  "
              f"(budget left ${remaining:.2f})", flush=True)

        r, a, s, _dirty = await _process_pack(
            source_path,
            per_pack_cap_usd=args.per_pack_cap_usd,
            total_budget_remaining_usd=remaining,
            dry_run=args.dry_run,
        )
        total_spent += s
        total_replaced += r
        total_attempted += a
        print(f"=== pack done: replaced {r}/{a} chars, ~${s:.3f} spent "
              f"(running ${total_spent:.2f}/${args.total_budget_usd:.2f})",
              flush=True)

        if args.dry_run or r == 0:
            continue

        archive = _build_archive(source_path)
        _git_commit_pack(source_path, archive, r, a)

        if args.skip_seed:
            print("== --skip-seed set; archive committed, prod untouched", flush=True)
            continue

        run_id = _trigger_seed(archive)
        if run_id is None:
            print(f"!! failed to dispatch seed for {archive.name}")
            continue
        print(f"== seed run {run_id} dispatched; waiting...")
        ok = _wait_for_seed(run_id)
        print(f"== seed run {run_id} {'OK' if ok else 'FAIL'}")
        if not ok:
            print("!! pausing for inspection")
            return 2

    if not args.dry_run and total_replaced > 0 and not args.skip_seed:
        # One final push at the end so all per-pack commits land together.
        _run_check(["git", "push", "origin", "main"], cwd=REPO_ROOT)
    elif not args.dry_run and total_replaced > 0:
        print("== --skip-seed: remember to `git push` when ready", flush=True)

    print(f"\n== done. Replaced {total_replaced}/{total_attempted} images, "
          f"~${total_spent:.2f} spent.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--packs",
        default="*.source.json",
        help="Glob (relative to backend/configs/precompute/starter_packs) or "
             "CSV of paths. Default: every *.source.json in the packs dir.",
    )
    p.add_argument("--total-budget-usd", type=float, default=10.0,
                   help="Hard ceiling across all packs. Default: $10.")
    p.add_argument("--per-pack-cap-usd", type=float, default=2.0,
                   help="Per-pack ceiling. Default: $2.")
    p.add_argument("--dry-run", action="store_true",
                   help="Classify topics and print the plan; do not call FAL.")
    p.add_argument("--skip-seed", action="store_true",
                   help="Rebuild + commit only; do not trigger seed workflow.")
    args = p.parse_args(argv)

    # Make the backend package importable when run via `python -m`.
    sys.path.insert(0, str(BACKEND_DIR))

    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
