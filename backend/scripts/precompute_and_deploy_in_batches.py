"""Incremental precompute + deploy orchestrator.

Generates topic content in small batches (default 50 per batch), evaluates
quality, signs archives, commits to git, and triggers the production seed
workflow after each batch. Designed to add up to N new topics within a
fixed dollar budget while keeping blast radius small.

----------------------------------------------------------------------------
Why this exists (analysis of the prior big-batch approach)
----------------------------------------------------------------------------

Up to 2026-05-03 we generated content in 250-topic mega-batches. The
batch-1 seed of 2026-05-03 surfaced three structural problems:

1. **Single bad byte poisons the whole batch.** A NUL emitted by the
   LLM in *one* character profile (``macram\\u0000``) caused PostgreSQL to
   reject the *entire* archive with HTTP 500. Recovery required a
   manual diff, a new commit, and a re-deploy.

   *Fix shipped*: ``scripts/build_starter_packs.sanitize_text`` strips
   C0 control bytes and the BOM during archive build, with regression
   tests asserting the signed bytes contain no NUL.

2. **No closed-loop verification after seeding.** The CI workflow
   accepted any HTTP 200 from the import endpoint as "success", but did
   not confirm the live pack count actually increased. A silent
   skip-existing path looked identical to a real seed.

   *Fix shipped*: ``scripts/prod_precompute_smoke.py`` + a new
   ``Verify production has published packs`` step in
   ``.github/workflows/seed-prod-packs.yml`` calls
   ``/api/v1/healthz/precompute`` after the import and asserts
   ``packs_published >= packs_inserted``.

3. **Mega-batches waste wallclock when one upstream call breaks.** A
   single Gemini timeout halfway through a 250-topic run meant either
   restarting from scratch or manually patching the source file.

   *Fix shipped*: this orchestrator commits + seeds every
   ``--batch-size`` topics (default 50). A failure inside batch N never
   loses the work in batches 1..N-1. Each batch is independently
   replayable.

----------------------------------------------------------------------------
Pipeline efficiency notes (current baseline → target)
----------------------------------------------------------------------------

Per-topic full cost from the 2026-05-03 batch-2 run (250 topics,
``$20.63`` total, ``$0.083/topic``):

  * generate_topic_pool       ~$0.005   (one shared LLM call per ~60 slugs)
  * content gen (4 LLM calls) ~$0.020
  * 2-judge consensus         ~$0.004
  * images (~6 chars × 47%
    judge-pass × 73%
    image-pass × $0.011)      ~$0.054

Where the budget actually goes:

  * **65% on images** of judge-passed topics. Each character is one
    fal.ai call; we currently regenerate every character even when a
    canonically-named row already exists in production. The dedup hook
    in ``app/services/precompute/dedup.py`` exists for the live worker
    but is *not* consulted by the offline image script.
    → Improvement opportunity (not shipped this session): tee
    ``find_media_asset_by_prompt_hash`` into the image script before
    spending fal credit.

  * **24% on text** of pool topics. Cheap, but the judge rejects ~53%.
    → Improvement opportunity (not shipped this session): use the
    judge's structured ``blocking_reasons`` to feed back into the
    topic-pool prompt (e.g. avoid heavily-licensed IP categories that
    consistently fail the IP guard).

  * **5% on judging**. Already cheap; not worth optimising.

  * **6% on pool generation**. Already cheap.

----------------------------------------------------------------------------
Usage
----------------------------------------------------------------------------

::

    python -m scripts.precompute_and_deploy_in_batches \\
        --target 500 \\
        --batch-size 50 \\
        --budget-usd 50 \\
        --start-batch 3

The script:

  1. Loads ``configs/precompute/starter_packs/all_seeded_slugs.json`` —
     the union of every slug already in production. Falls back to
     scanning every ``starter_ranked_candidates_*.source.json`` in the
     repo when the file is missing.
  2. Loops:
     a. Generates a pool of ``batch_size * 1.5`` candidate slugs that
        are NOT in the seen set.
     b. Generates content (synopsis + characters + baseline questions),
        runs the two-judge consensus.
     c. Generates images for judge-passed topics.
     d. Builds the signed archive (sanitiser scrubs any NUL byte).
     e. Commits the archive, sig, source, report and updated seed-set
        to git, pushes to ``main``.
     f. Dispatches ``Seed prod precompute packs`` workflow targeting
        the new archive, polls for completion, asserts success.
     g. Updates the spend ledger; if exceeded, exits cleanly with the
        running total reported.

The script is idempotent across re-runs: ``--start-batch`` resumes
without overwriting earlier batches' files, and the seen-slug set
accumulates across batches.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess  # nosec B404 — orchestrator runs trusted local scripts
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKS_DIR = BACKEND_ROOT / "configs" / "precompute" / "starter_packs"
SEEN_SLUGS_PATH = PACKS_DIR / "all_seeded_slugs.json"

# Load secrets from backend/.env so subprocesses inherit them (HMAC_SECRET,
# GEMINI_API_KEY, FAL_AI_KEY, OPERATOR_TOKEN). Best-effort: if python-dotenv
# isn't installed the subprocesses each load .env independently.
try:  # pragma: no cover — env wiring
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(BACKEND_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

PYTHON = sys.executable
SEED_WORKFLOW = "Seed prod precompute packs"
DEFAULT_API_URL = (
    "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"
)

# Conservative cost model (matches scripts/_precompute_spend.py and
# observed batch-2 actuals). Used for client-side budget tracking only.
COST_PER_TEXT_TOPIC_USD = 0.020 + 0.004  # 4 LLM calls + 2 judges
COST_PER_IMAGE_USD = 0.011

# Slug pattern used by ``build_starter_packs`` is kebab-case ASCII.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# Spend tracking
# ---------------------------------------------------------------------------


@dataclass
class SpendTracker:
    cap_usd: float
    spent_usd: float = 0.0
    batches: list[dict[str, Any]] = field(default_factory=list)

    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def can_afford(self, projected_usd: float) -> bool:
        return (self.spent_usd + projected_usd) <= self.cap_usd

    def record_batch(self, *, batch_id: int, text_usd: float, image_usd: float, topics_added: int) -> None:
        total = text_usd + image_usd
        self.spent_usd += total
        self.batches.append(
            {
                "batch_id": batch_id,
                "text_usd": round(text_usd, 4),
                "image_usd": round(image_usd, 4),
                "total_usd": round(total, 4),
                "topics_added": topics_added,
                "cumulative_usd": round(self.spent_usd, 4),
            }
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "cap_usd": self.cap_usd,
            "spent_usd": round(self.spent_usd, 4),
            "remaining_usd": round(self.remaining_usd(), 4),
            "batches": list(self.batches),
        }


# ---------------------------------------------------------------------------
# Slug accounting
# ---------------------------------------------------------------------------


def load_seen_slugs() -> set[str]:
    """Return the union of every slug already known to production.

    Reads ``all_seeded_slugs.json`` if present, otherwise scans every
    ``*.source.json`` in ``configs/precompute/starter_packs/``. New
    batches always update the cached file so subsequent runs are O(1).
    """
    if SEEN_SLUGS_PATH.exists():
        try:
            data = json.loads(SEEN_SLUGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(s).strip() for s in data if str(s).strip()}
        except json.JSONDecodeError:
            pass

    seen: set[str] = set()
    for src in PACKS_DIR.glob("*.source.json"):
        try:
            doc = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        topics = doc.get("topics") if isinstance(doc, dict) else None
        if not isinstance(topics, list):
            continue
        for t in topics:
            slug = str((t or {}).get("slug") or "").strip()
            if slug:
                seen.add(slug)
    return seen


def write_seen_slugs(seen: set[str]) -> None:
    PACKS_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_SLUGS_PATH.write_text(
        json.dumps(sorted(seen), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def collect_archive_slugs(archive_path: Path) -> list[str]:
    doc = json.loads(archive_path.read_text(encoding="utf-8"))
    out: list[str] = []
    for pack in doc.get("packs", []) or []:
        topic = (pack or {}).get("topic") or {}
        slug = str(topic.get("slug") or "").strip()
        if slug and _SLUG_RE.match(slug):
            out.append(slug)
    return out


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path = BACKEND_ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run ``cmd`` synchronously, stream stdout to console, raise on non-zero."""
    print(f"$ {' '.join(cmd)}")
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(  # noqa: S603 — trusted local scripts
        cmd, cwd=str(cwd), env=full_env, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"command failed (rc={proc.returncode}): {' '.join(cmd)}")
    return proc


def _git_commit_and_push(message: str, paths: list[Path]) -> None:
    """Stage ``paths``, commit with ``message``, push to origin/main."""
    rel_paths = [str(p.relative_to(REPO_ROOT)) for p in paths if p.exists()]
    if not rel_paths:
        print("nothing to commit")
        return
    _run(["git", "add", *rel_paths], cwd=REPO_ROOT)
    # Skip empty commits gracefully.
    status = subprocess.run(  # noqa: S603,S607
        ["git", "diff", "--cached", "--quiet"], cwd=str(REPO_ROOT), check=False
    )
    if status.returncode == 0:
        print("no staged changes — skipping commit")
        return
    _run(["git", "commit", "-m", message], cwd=REPO_ROOT)
    _run(["git", "push", "origin", "main"], cwd=REPO_ROOT)


def _gh(*args: str, capture: bool = False) -> str:
    cmd = ["gh", *args]
    print(f"$ {' '.join(cmd)}")
    if capture:
        out = subprocess.run(  # noqa: S603,S607
            cmd, cwd=str(REPO_ROOT), check=True, capture_output=True, text=True
        )
        return out.stdout
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)  # noqa: S603,S607
    return ""


# ---------------------------------------------------------------------------
# Per-batch pipeline
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    batch_id: int
    archive_path: Path
    packs_in_archive: int
    text_usd: float
    image_usd: float
    seed_run_id: int | None
    seed_succeeded: bool


async def _run_batch(  # noqa: C901 — orchestrator: branching is inherent
    *,
    batch_id: int,
    batch_size: int,
    seen_slugs: set[str],
    secret_env: str,
    api_url: str,
    text_cap_usd: float,
    image_cap_usd: float,
    archive_basename: str,
) -> BatchResult:
    """Run one batch end-to-end. See module docstring for the full flow."""
    print(f"\n{'='*72}\n=== Batch {batch_id}: target={batch_size} topics, "
          f"text cap=${text_cap_usd:.2f}, image cap=${image_cap_usd:.2f}\n{'='*72}")

    # Per-batch file paths.
    pool_path = PACKS_DIR / f"{archive_basename}.pool.json"
    source_path = PACKS_DIR / f"{archive_basename}.source.json"
    report_path = PACKS_DIR / f"{archive_basename}.report.json"
    archive_path = PACKS_DIR / f"{archive_basename}.json"
    sig_path = PACKS_DIR / f"{archive_basename}.json.sig"
    excludes_path = PACKS_DIR / f"{archive_basename}.excludes.json"

    # 1. Persist current seen-slug set as the exclusion list.
    excludes_path.write_text(
        json.dumps(sorted(seen_slugs), ensure_ascii=False), encoding="utf-8"
    )

    # 2. Generate a pool of candidate topics (oversample 1.5x to absorb
    #    judge rejection + dedup loss).
    pool_target = max(int(batch_size * 1.5), batch_size + 5)
    _run(
        [
            PYTHON,
            "-m",
            "scripts.generate_topic_pool",
            "--target",
            str(pool_target),
            "--seed",
            str(100 + batch_id),
            "--exclude-slugs-file",
            str(excludes_path),
            "--out",
            str(pool_path),
        ]
    )

    # 3. Generate ranked content + run judge.
    text_budget_arg = max(0.05, round(text_cap_usd, 2))
    _run(
        [
            PYTHON,
            "-m",
            "scripts.generate_ranked_pack_candidates",
            "--limit",
            str(batch_size),
            "--budget-usd",
            str(text_budget_arg),
            "--judge",
            "--spend-cap-usd",
            str(text_budget_arg),
            "--topic-pool",
            str(pool_path),
            "--out",
            str(source_path),
            "--report-out",
            str(report_path),
        ]
    )

    # Compute observed text spend from report (fallback to estimate).
    text_spent_usd = _extract_text_spend_usd(report_path) or _estimate_text_spend_usd(source_path)

    # 4. Generate images for judge-passed topics.
    image_budget_arg = max(0.10, round(image_cap_usd, 2))
    _run(
        [
            PYTHON,
            "-m",
            "scripts.generate_images_for_packs",
            "--source",
            str(source_path),
            "--report",
            str(report_path),
            "--out",
            str(source_path),
            "--spend-cap-usd",
            str(image_budget_arg),
        ]
    )
    image_spent_usd = _estimate_image_spend_usd(source_path)

    # 5. Build the signed archive (sanitiser scrubs any NUL bytes).
    _run(
        [
            PYTHON,
            "-m",
            "scripts.build_starter_packs",
            "--source",
            str(source_path),
            "--out",
            str(archive_path),
            "--secret-env",
            secret_env,
        ]
    )

    archive_slugs = collect_archive_slugs(archive_path)
    print(f"== archive contains {len(archive_slugs)} packs")

    # Sanity gate: refuse to commit + seed an empty / under-sized archive.
    # 30% floor: prefer keeping a smaller-but-real batch over discarding work.
    min_acceptable = max(1, int(batch_size * 0.3))
    if len(archive_slugs) < min_acceptable:
        raise RuntimeError(
            f"batch {batch_id}: archive has {len(archive_slugs)} packs "
            f"(below floor of {min_acceptable}); aborting before commit"
        )

    # 6. Commit + push.
    _git_commit_and_push(
        f"feat(precompute): batch {batch_id} — {len(archive_slugs)} new topics",
        [archive_path, sig_path, source_path, report_path, pool_path, excludes_path, SEEN_SLUGS_PATH],
    )

    # 7. Trigger seed workflow + wait for completion.
    seed_run_id, seed_ok = _trigger_and_wait_for_seed(
        archive_repo_path=f"backend/configs/precompute/starter_packs/{archive_path.name}",
    )

    return BatchResult(
        batch_id=batch_id,
        archive_path=archive_path,
        packs_in_archive=len(archive_slugs),
        text_usd=text_spent_usd,
        image_usd=image_spent_usd,
        seed_run_id=seed_run_id,
        seed_succeeded=seed_ok,
    )


def _extract_text_spend_usd(report_path: Path) -> float | None:
    """Best-effort: read ``spend_usd`` (or ``spent_usd``) from the report."""
    if not report_path.exists():
        return None
    try:
        doc = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for key in ("spend_usd", "spent_usd", "total_usd"):
        v = doc.get(key) if isinstance(doc, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
    spend = (doc.get("spend") if isinstance(doc, dict) else {}) or {}
    for key in ("spend_usd", "spent_usd", "total_usd"):
        v = spend.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _estimate_text_spend_usd(source_path: Path) -> float:
    if not source_path.exists():
        return 0.0
    try:
        doc = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0.0
    n = len(doc.get("topics", []) or [])
    return round(n * COST_PER_TEXT_TOPIC_USD, 4)


def _estimate_image_spend_usd(source_path: Path) -> float:
    if not source_path.exists():
        return 0.0
    try:
        doc = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0.0
    image_count = 0
    for t in doc.get("topics", []) or []:
        for c in (t or {}).get("characters", []) or []:
            if (c or {}).get("image_url"):
                image_count += 1
    return round(image_count * COST_PER_IMAGE_USD, 4)


def _trigger_and_wait_for_seed(*, archive_repo_path: str) -> tuple[int | None, bool]:
    """Dispatch the seed workflow and poll until it completes.

    Returns ``(run_id, succeeded)``. The seed workflow itself is a small
    curl + smoke check; it normally completes in < 90s.
    """
    _gh(
        "workflow",
        "run",
        SEED_WORKFLOW,
        "-f",
        f"archive_path={archive_repo_path}",
        "-f",
        "force_upgrade=true",
    )
    # Give GitHub a few seconds to register the dispatch before polling.
    time.sleep(8)

    run_id: int | None = None
    deadline = time.time() + 8 * 60  # 8 minutes max wait
    while time.time() < deadline:
        raw = _gh(
            "run",
            "list",
            "--workflow=seed-prod-packs.yml",
            "--limit",
            "5",
            "--json",
            "databaseId,status,conclusion,createdAt",
            capture=True,
        )
        try:
            runs = json.loads(raw)
        except json.JSONDecodeError:
            runs = []
        # Highest-id (most recent) run that's already past dispatch.
        runs_sorted = sorted(runs, key=lambda r: r.get("databaseId", 0), reverse=True)
        if runs_sorted:
            top = runs_sorted[0]
            run_id = int(top.get("databaseId") or 0) or run_id
            status = top.get("status")
            conclusion = top.get("conclusion")
            print(f"  seed run {run_id}: status={status} conclusion={conclusion}")
            if status == "completed":
                return run_id, conclusion == "success"
        time.sleep(20)

    return run_id, False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:
    if shutil.which("gh") is None:
        print("ERROR: gh CLI not found on PATH", file=sys.stderr)
        return 1
    if not (os.getenv(args.secret_env) or "").strip():
        print(f"ERROR: env var {args.secret_env} is empty", file=sys.stderr)
        return 1

    seen = load_seen_slugs()
    print(f"== loaded {len(seen)} seen slugs from {SEEN_SLUGS_PATH.name}")

    spend = SpendTracker(cap_usd=args.budget_usd)
    target_remaining = args.target
    batch_id = args.start_batch
    summary_path = PACKS_DIR / f"orchestrator_run_{int(time.time())}.summary.json"

    while target_remaining > 0:
        # Per-batch budget = min(remaining_total / remaining_batches, hard cap)
        # To keep cost predictable across batches.
        est_batches_left = max(1, (target_remaining + args.batch_size - 1) // args.batch_size)
        # Per-batch hard cap covers worst-case (50 topics × ($0.024 text + 5×$0.011 image) × 1.4 ≈ $5.5).
        per_batch_hard_cap = args.batch_size * (COST_PER_TEXT_TOPIC_USD + 5 * COST_PER_IMAGE_USD) * 1.4
        # Floor each batch at $2.50 so text+image gen has room to land a full pack;
        # observed empirical spend is ~$1.13/batch but the pre-flight estimator
        # in generate_images_for_packs aborts early if cap is below the next char's cost.
        per_batch_floor = 2.50
        batch_budget = min(
            max(spend.remaining_usd() / est_batches_left, per_batch_floor),
            per_batch_hard_cap,
            spend.remaining_usd(),
        )
        if batch_budget < 0.5:
            print(f"== budget exhausted (${spend.spent_usd:.2f} / ${spend.cap_usd:.2f})")
            break
        # Reserve ~30% for images (judge rejects ~half, surviving topics
        # average ~5 chars × $0.011 ≈ $0.055/topic).
        text_cap = round(batch_budget * 0.6, 2)
        image_cap = round(batch_budget * 0.4, 2)

        this_batch_size = min(args.batch_size, target_remaining)
        archive_basename = f"starter_ranked_candidates_batch{batch_id}"

        # Retry transient failures (mostly Gemini Responses-API parse errors
        # in topic_pool gen) up to 2 times before bailing the orchestrator.
        result = None
        for attempt in range(1, 4):
            try:
                result = await _run_batch(
                    batch_id=batch_id,
                    batch_size=this_batch_size,
                    seen_slugs=seen,
                    secret_env=args.secret_env,
                    api_url=args.api_url,
                    text_cap_usd=text_cap,
                    image_cap_usd=image_cap,
                    archive_basename=archive_basename,
                )
                break
            except Exception as exc:  # noqa: BLE001 — operator tool
                print(
                    f"!! batch {batch_id} attempt {attempt}/3 failed: {exc}",
                    file=sys.stderr,
                )
                if attempt == 3:
                    print(
                        f"!! batch {batch_id} exhausted retries; bailing",
                        file=sys.stderr,
                    )
                    return 2
                # Clear partial artifacts so the retry starts clean.
                for ext in (".json", ".json.sig", ".source.json", ".report.json", ".pool.json", ".excludes.json"):
                    p = PACKS_DIR / f"{archive_basename}{ext}"
                    if p.exists():
                        try:
                            p.unlink()
                        except OSError:
                            pass
                await asyncio.sleep(5)
        assert result is not None  # for type checker

        # Update slug accounting.
        new_slugs = set(collect_archive_slugs(result.archive_path))
        added_slugs = new_slugs - seen
        seen.update(new_slugs)
        write_seen_slugs(seen)

        spend.record_batch(
            batch_id=batch_id,
            text_usd=result.text_usd,
            image_usd=result.image_usd,
            topics_added=len(added_slugs),
        )
        target_remaining -= len(added_slugs)
        print(
            f"== batch {batch_id} done: +{len(added_slugs)} topics "
            f"(spent ${spend.spent_usd:.2f} / ${spend.cap_usd:.2f}, "
            f"remaining target {target_remaining}, "
            f"seed={'OK' if result.seed_succeeded else 'FAIL'})"
        )

        summary_path.write_text(
            json.dumps(
                {
                    "target": args.target,
                    "remaining": target_remaining,
                    "spend": spend.snapshot(),
                    "total_seen_slugs": len(seen),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if not result.seed_succeeded:
            print("!! seed run did not succeed — pausing the orchestrator")
            return 3

        batch_id += 1

    print(f"\n=== Orchestrator done. Spent ${spend.spent_usd:.2f}. "
          f"Total seen slugs: {len(seen)}. Summary: {summary_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", type=int, default=500, help="total NEW topics to add")
    p.add_argument("--batch-size", type=int, default=50, help="topics per commit + seed cycle")
    p.add_argument("--budget-usd", type=float, default=50.0, help="hard $ cap across the run")
    p.add_argument("--start-batch", type=int, default=3, help="batch id to start from (used for filenames)")
    p.add_argument("--secret-env", default="PRECOMPUTE_HMAC_SECRET")
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    return asyncio.run(_amain(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
