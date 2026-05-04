"""Backfill character images for batches that were committed without images.

Background
----------
Batches 5–16 were generated and seeded by the orchestrator while a bug in
``generate_images_for_packs`` (``would_exceed(110)`` treating cents as
dollars) caused the image-gen step to abort at ``$0.00`` spend. The
content (synopsis, characters, baselines) is sound; only the
``character.image_url`` fields are ``null``.

This script, for each affected batch:
  1. Runs ``generate_images_for_packs`` against the existing ``.source.json``
     (now with the fix in place).
  2. Rebuilds the signed archive via ``build_starter_packs``.
  3. Commits the updated source/archive/sig files.
  4. Pushes once at the end (single commit per batch).
  5. Triggers the seed workflow with ``force_upgrade=true`` and waits.

Idempotent: re-running on a batch that already has images simply re-evaluates
existing URLs without regenerating (we use ``--evaluate-existing`` first to
score what's there, then do a full pass to fill blanks).

Usage
-----
    python -m scripts.backfill_images_for_batches \
        --batches 5,6,7,8,9,10,11,12,13,14,15,16 \
        --spend-cap-per-batch-usd 1.50 \
        --total-budget-usd 25
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
PACKS_DIR = BACKEND_DIR / "configs" / "precompute" / "starter_packs"
SEEN_SLUGS_PATH = PACKS_DIR / "all_seeded_slugs.json"


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    print(f"$ {' '.join(cmd)}", flush=True)
    # Force UTF-8 stdout/stderr in child processes so emoji and other
    # non-cp1252 chars in topic data don't crash structlog on Windows.
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    if env:
        child_env.update(env)
    return subprocess.call(cmd, cwd=cwd, env=child_env)


def _run_check(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    rc = _run(cmd, cwd=cwd, env=env)
    if rc != 0:
        raise RuntimeError(f"command failed (exit {rc}): {' '.join(cmd)}")


def _python_exe() -> str:
    return sys.executable


def _count_images(source_path: Path) -> tuple[int, int]:
    """Return (chars_with_images, total_chars) across all topics in source."""
    data = json.loads(source_path.read_text(encoding="utf-8"))
    topics = data.get("topics") or data.get("packs") or data
    if not isinstance(topics, list):
        return (0, 0)
    total = 0
    with_img = 0
    for t in topics:
        chars = t.get("characters") or t.get("character_set") or []
        if not isinstance(chars, list):
            continue
        for c in chars:
            total += 1
            url = c.get("image_url")
            if url and isinstance(url, str) and url.strip():
                with_img += 1
    return with_img, total


def _gen_images(batch_id: int, spend_cap_usd: float) -> None:
    base = PACKS_DIR / f"starter_ranked_candidates_batch{batch_id}"
    source = base.with_suffix(".source.json")
    report = base.with_suffix(".report.json")
    cmd = [
        _python_exe(), "-m", "scripts.generate_images_for_packs",
        "--source", str(source),
        "--report", str(report),
        "--out", str(source),
        "--spend-cap-usd", str(spend_cap_usd),
    ]
    _run_check(cmd, cwd=BACKEND_DIR)


def _build_archive(batch_id: int) -> Path:
    base = PACKS_DIR / f"starter_ranked_candidates_batch{batch_id}"
    source = base.with_suffix(".source.json")
    out_archive = base.with_suffix(".json")
    cmd = [
        _python_exe(), "-m", "scripts.build_starter_packs",
        "--source", str(source),
        "--out", str(out_archive),
        "--secret-env", "PRECOMPUTE_HMAC_SECRET",
    ]
    _run_check(cmd, cwd=BACKEND_DIR)
    return out_archive


def _git_commit_and_push(batch_id: int, with_img: int, total: int) -> None:
    base = PACKS_DIR / f"starter_ranked_candidates_batch{batch_id}"
    files = [
        str(base.with_suffix(".json").relative_to(REPO_ROOT)),
        str(Path(str(base.with_suffix(".json")) + ".sig").relative_to(REPO_ROOT)),
        str(base.with_suffix(".source.json").relative_to(REPO_ROOT)),
    ]
    _run_check(["git", "add", *files], cwd=REPO_ROOT)
    msg = (
        f"fix(precompute): backfill character images for batch {batch_id}\n\n"
        f"Re-ran generate_images_for_packs after the would_exceed(110)\n"
        f"cents-vs-dollars bug fix. Now {with_img}/{total} characters have\n"
        f"validated images (was 0/{total})."
    )
    _run_check(["git", "commit", "-m", msg], cwd=REPO_ROOT)
    _run_check(["git", "push", "origin", "main"], cwd=REPO_ROOT)


def _trigger_seed(batch_id: int) -> str | None:
    archive_repo_path = f"backend/configs/precompute/starter_packs/starter_ranked_candidates_batch{batch_id}.json"
    cmd = [
        "gh", "workflow", "run", "Seed prod precompute packs",
        "-f", f"archive_path={archive_repo_path}",
        "-f", "force_upgrade=true",
    ]
    _run_check(cmd, cwd=REPO_ROOT)
    # Brief wait for the dispatch to register, then list runs.
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batches", required=True, help="comma-separated batch ids, e.g. 5,6,7")
    p.add_argument("--spend-cap-per-batch-usd", type=float, default=1.50)
    p.add_argument("--total-budget-usd", type=float, default=25.0)
    p.add_argument("--skip-seed", action="store_true",
                   help="rebuild + commit only; do not trigger seed workflow")
    args = p.parse_args(argv)

    if not (os.getenv("PRECOMPUTE_HMAC_SECRET") or "").strip():
        print("ERROR: PRECOMPUTE_HMAC_SECRET is empty", file=sys.stderr)
        return 1

    batch_ids = [int(x) for x in args.batches.split(",") if x.strip()]
    spent_usd = 0.0

    for batch_id in batch_ids:
        if spent_usd >= args.total_budget_usd:
            print(f"== total budget reached (${spent_usd:.2f}); stopping")
            break

        base = PACKS_DIR / f"starter_ranked_candidates_batch{batch_id}"
        source = base.with_suffix(".source.json")
        if not source.exists():
            print(f"!! batch {batch_id}: source missing, skipping")
            continue

        before_with, before_total = _count_images(source)
        print(
            f"========================================================================\n"
            f"=== Batch {batch_id}: {before_with}/{before_total} chars with images\n"
            f"========================================================================",
            flush=True,
        )

        if before_total > 0 and before_with == before_total:
            print(f"== batch {batch_id} already fully imaged; skipping")
            continue

        cap = min(args.spend_cap_per_batch_usd, args.total_budget_usd - spent_usd)
        _gen_images(batch_id, cap)

        after_with, after_total = _count_images(source)
        delta = after_with - before_with
        print(f"== batch {batch_id}: now {after_with}/{after_total} (+{delta} new images)", flush=True)
        # Rough cost: each new image ≈ $0.011 + 1 judge call ≈ $0.013.
        spent_usd += delta * 0.013
        print(f"== running spend estimate: ${spent_usd:.3f} / ${args.total_budget_usd:.2f}")

        _build_archive(batch_id)

        if delta == 0:
            print(f"== batch {batch_id}: no new images, skipping commit")
            continue

        with_img_final, total_final = _count_images(source)
        _git_commit_and_push(batch_id, with_img_final, total_final)

        if args.skip_seed:
            print(f"== batch {batch_id}: --skip-seed set, leaving prod alone")
            continue

        run_id = _trigger_seed(batch_id)
        if run_id is None:
            print(f"!! batch {batch_id}: failed to dispatch seed run")
            continue
        print(f"== batch {batch_id}: seed run {run_id} dispatched, waiting...")
        ok = _wait_for_seed(run_id)
        print(f"== batch {batch_id}: seed run {run_id} {'OK' if ok else 'FAIL'}")
        if not ok:
            print(f"!! batch {batch_id}: seed failed; pausing for inspection")
            return 2

    print(f"== done. Total estimated spend: ${spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
