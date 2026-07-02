"""Pre-compute ANSWER-OPTION images for published packs (Phase 5 pool builder).

Builds the answer-image pool the flag-ON frontend renders: for a curated slice
of high-intent published packs, this script

  1. loads the pack the resolver actually serves (``topics.current_pack_id``,
     ``status='published'``) and its baseline questions,
  2. runs the QUESTION-level relevance gate (same bge-small embedder +
     thresholds as the build hook) so only depictable answer sets spend,
  3. generates same-universe images for gate-passing questions through
     ``FalLedger.guarded_generate`` (lifetime $-cap, dedup reuse, no phantom
     charges) via the existing ``QaImageGenerator`` — STRICT all-or-none per
     question (every option gets an image or none do),
  4. REHOSTS the bytes: downloads each fresh FAL CDN URL into
     ``media_assets.bytes_blob`` and rewrites the bound URL to the durable
     ``{api_base}/api/v1/media/{asset_id}`` endpoint (FAL CDN URLs expire;
     the media endpoint serves content-addressed bytes with immutable cache),
  5. writes the enriched ``options.items[*].image_url``/``image_alt`` back to
     the ``questions`` row ONLY when EVERY option in the question rehosted
     successfully (all-or-none enforced again at persist time).

Zero quiz-time latency: everything happens here, at pool-build time. The
serve path (``hydrator._resolve_baseline_questions`` → ``/quiz/status``)
passes ``image_url`` through verbatim.

Stem images are intentionally OFF (``stem_images=False``): the precompute
serve path only surfaces per-option images today, so a stem image would be
paid-for but never rendered.

Safety rails:
  * EVERY FAL call goes through the persistent ledger (lifetime cap).
  * ``--budget-usd`` is a PER-RUN stop on top of the lifetime cap.
  * ``--dry-run`` scores the gate and prints per-pack clear-rates without
    calling FAL or writing anything.
  * Idempotent: options that already carry ``image_url`` are reused as-is;
    identical prompts dedup against ``media_assets`` for $0.
  * Verified in prod: no ``questions`` row is shared across topics (dedup by
    text_hash produced zero cross-topic collisions), so per-question writes
    cannot leak one topic's imagery into another.

Usage (from ``backend/``, venv active, FAL_KEY or FAL_AI_KEY in env):

    # Gate-only preview, $0:
    python -m scripts.generate_answer_images --dry-run

    # Validation slice (a couple of packs):
    python -m scripts.generate_answer_images --slugs hogwarts-house,disney-princess

    # Full curated slice with a per-run stop:
    python -m scripts.generate_answer_images --budget-usd 8.0

Requires ``PROD_DB_URL`` (or ``DATABASE_URL``) in env, e.g. from
``az keyvault secret show --vault-name quizzical-shared-kv --name database-url
--query value -o tsv``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Curated high-intent slice: slug -> universe anchor for the image prompt.
# The topic's ``display_name`` in prod is usually a quiz TITLE ("Which Hogwarts
# House Do You Belong In?") — a poor universe anchor — so the anchor is named
# explicitly here. Overridable with --slugs (falls back to a prettified slug
# for unmapped ones).
# ---------------------------------------------------------------------------
CURATED_SLICE: dict[str, str] = {
    "hogwarts-house": "Harry Potter",
    "disney-princess": "Disney Princess",
    "star-wars-character": "Star Wars",
    "pokemon-starter": "Pokemon",
    "pokemon-type": "Pokemon",
    "friends-character": "the TV show Friends",
    "the-office-character": "the TV show The Office",
    "stranger-things-character": "Stranger Things",
    "game-of-thrones-house": "Game of Thrones",
    "lord-of-the-rings-race": "The Lord of the Rings",
    "avengers-original-six": "Marvel's Avengers",
    "greek-god": "Greek mythology",
    "norse-deity": "Norse mythology",
    "ancient-egyptian-god": "ancient Egyptian mythology",
    "dnd-class": "Dungeons and Dragons",
    "jurassic-park-dinosaur": "Jurassic Park",
    "super-mario-character": "Super Mario",
    "legend-of-zelda-race": "The Legend of Zelda",
    "mario-kart-racer": "Mario Kart",
    "studio-ghibli-protagonist": "Studio Ghibli films",
    "dog-breed-match": "dog breeds",
    "cat-breed-personality": "cat breeds",
    "coffee-personality": "coffee drinks",
    "classic-cocktail-style": "classic cocktails",
    "dessert-personality": "desserts",
}

DEFAULT_API_BASE = (
    "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"
)


def _normalize_dsn(raw: str) -> str:
    """asyncpg needs ssl via ``connect_args``; strip ``?sslmode=...`` + driver."""
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _prettify_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("-", " ").split())


@dataclass
class PackResult:
    slug: str
    universe: str
    n_questions: int = 0
    n_cleared: int = 0
    n_committed: int = 0
    n_options_written: int = 0
    generated: int = 0
    reused: int = 0
    gated_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    examples: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "universe": self.universe,
            "questions": self.n_questions,
            "questions_cleared_gate": self.n_cleared,
            "questions_committed": self.n_committed,
            "options_written": self.n_options_written,
            "images_generated": self.generated,
            "images_reused": self.reused,
            "strings_gated_out": self.gated_out,
            "cost_usd": round(self.cost_usd, 4),
            "error": self.error,
        }


class _SizedClient:
    """Wraps ``FalImageClient`` pinning model/size/steps for answer tiles.

    ``QaImageGenerator`` calls ``generate(prompt=, negative_prompt=, seed=)``
    without a size, which would fall back to the 256px cast-thumb default.
    Answer tiles render larger than cast thumbs, so pin an explicit square
    size (schnell is billed per-megapixel, so 4 steps costs the same)."""

    def __init__(self, inner: Any, *, model: str, size: int) -> None:
        self._inner = inner
        self._model = model
        self._size = {"width": int(size), "height": int(size)}

    async def generate(self, *, prompt: str, negative_prompt: str | None = None,
                       seed: int | None = None) -> str | None:
        return await self._inner.generate(
            prompt,
            negative_prompt=negative_prompt,
            model=self._model,
            image_size=dict(self._size),
            num_inference_steps=4,
            timeout_s=30.0,
            seed=seed,
        )


async def _load_pack_questions(session, slug: str):
    """Return ``(pack_id, [Question rows])`` for the pack the resolver serves,
    or ``(None, [])`` when the topic/pack/BQS is missing or unpublished."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.db import BaselineQuestionSet, Question, Topic, TopicPack

    topic = (
        await session.execute(select(Topic).where(Topic.slug == slug))
    ).scalars().first()
    if topic is None or topic.current_pack_id is None:
        return None, []
    pack = (
        await session.execute(
            select(TopicPack).where(
                TopicPack.id == topic.current_pack_id,
                TopicPack.status == "published",
            )
        )
    ).scalar_one_or_none()
    if pack is None or pack.baseline_question_set_id is None:
        return None, []
    bqs = (
        await session.execute(
            select(BaselineQuestionSet).where(
                BaselineQuestionSet.id == pack.baseline_question_set_id
            )
        )
    ).scalar_one_or_none()
    if bqs is None or not isinstance(bqs.composition, dict):
        return pack.id, []
    q_ids = []
    for raw in bqs.composition.get("question_ids") or []:
        try:
            q_ids.append(_uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not q_ids:
        return pack.id, []
    rows = (
        await session.execute(select(Question).where(Question.id.in_(q_ids)))
    ).scalars().all()
    by_id = {q.id: q for q in rows}
    return pack.id, [by_id[qid] for qid in q_ids if qid in by_id]


def _artefact_for(universe: str, slug: str, questions) -> tuple[dict, list[tuple[Any, list[dict]]]]:
    """Build the enrichment artefact. Returns ``(artefact, [(row, items)])``
    where ``items`` are the mutable option-dict copies bound into the artefact
    (mutated in place by the generator, then written back to ``row``)."""
    art_questions: list[dict] = []
    row_items: list[tuple[Any, list[dict]]] = []
    for q in questions:
        opts = q.options if isinstance(q.options, dict) else {}
        items = opts.get("items")
        if not isinstance(items, list) or not items:
            continue
        copies = [dict(it) for it in items if isinstance(it, dict)]
        if len(copies) != len(items):
            continue  # unexpected shape — leave this question untouched
        art_questions.append({"question_text": q.text, "options": copies})
        row_items.append((q, copies))
    artefact = {
        "topic": {"display_name": universe, "slug": slug},
        "questions": art_questions,
    }
    return artefact, row_items


async def _rehost_option_urls(
    session, items: list[dict], *, api_base: str, http
) -> bool:
    """Download bytes for every freshly-bound FAL URL in ``items`` and rewrite
    each ``image_url`` to the durable ``/api/v1/media/{id}`` URL. Returns True
    only when EVERY option ends up with a durable URL (all-or-none)."""
    from sqlalchemy import select

    from app.models.db import MediaAsset

    for it in items:
        url = it.get("image_url")
        if not (isinstance(url, str) and url.strip()):
            return False
        if "/api/v1/media/" in url:
            continue  # already durable (idempotent re-run / dedup reuse)
        asset = (
            await session.execute(
                select(MediaAsset).where(MediaAsset.storage_uri == url).limit(1)
            )
        ).scalars().first()
        if asset is None:
            print("    ! no media_assets row for bound URL; skipping question", flush=True)
            return False
        if asset.bytes_blob is None:
            try:
                resp = await http.get(url, timeout=45.0, follow_redirects=True)
            except Exception as exc:  # noqa: BLE001 — any fetch problem fails the question
                print(f"    ! download failed: {type(exc).__name__}", flush=True)
                return False
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if resp.status_code != 200 or not ctype.startswith("image/") or not resp.content:
                print(
                    f"    ! bad image response status={resp.status_code} type={ctype}",
                    flush=True,
                )
                return False
            asset.bytes_blob = resp.content
            payload = dict(asset.prompt_payload or {})
            payload["content_type"] = ctype
            asset.prompt_payload = payload
        durable = f"{api_base.rstrip('/')}/api/v1/media/{asset.id}"
        asset.storage_uri = durable  # future dedup reuse binds the durable URL
        it["image_url"] = durable
        await session.flush()
    return True


async def _process_pack(
    session, *, slug: str, universe: str, api_base: str,
    dry_run: bool, gate, images_cfg, http, run_state: dict,
) -> PackResult:
    from sqlalchemy.orm.attributes import flag_modified

    res = PackResult(slug=slug, universe=universe)
    pack_id, questions = await _load_pack_questions(session, slug)
    if not questions:
        res.error = "no_published_pack_or_questions"
        return res

    artefact, row_items = _artefact_for(universe, slug, questions)
    res.n_questions = len(row_items)

    # Gate preview (also the whole story for --dry-run).
    for q_art in artefact["questions"]:
        texts = [o.get("text") for o in q_art["options"]
                 if isinstance(o.get("text"), str) and o.get("text").strip()]
        qd = await gate.score_question(texts)
        if qd.generate:
            res.n_cleared += 1
    if dry_run:
        return res

    from app.core.config import settings
    from app.services.icons.fal_ledger import FalLedger
    from app.services.icons.qa_pipeline import QaImageGenerator
    from app.services.image_service import _client_singleton

    image_gen = settings.image_gen
    size = int(run_state["image_size"])
    cfg = SimpleNamespace(
        provider=getattr(image_gen, "provider", "fal"),
        model=getattr(image_gen, "model", "fal-ai/flux/schnell"),
        image_size={"width": size, "height": size},
        style_suffix=getattr(image_gen, "style_suffix", ""),
        negative_prompt=getattr(image_gen, "negative_prompt", ""),
    )
    ledger = FalLedger(session, config=images_cfg.fal_budget)
    spent_before = await ledger.total_spent_micros()

    gen = QaImageGenerator(
        session=session,
        ledger=ledger,
        client=_SizedClient(_client_singleton, model=cfg.model, size=size),
        image_gen_cfg=cfg,
        gate=gate,
        style_suffix=getattr(images_cfg, "qa_style_suffix", "") or None,
        stem_images=False,  # serve path renders per-OPTION images only
    )
    stats = await gen.enrich(artefact)
    res.generated = stats.generated
    res.reused = stats.reused
    res.gated_out = stats.gated_out
    res.cost_usd = stats.cost_micros / 100_000.0
    res.examples = list(stats.examples)
    run_state["run_spent_micros"] += max(
        0, await ledger.total_spent_micros() - spent_before
    )

    # Persist: all-or-none per question, rehosted to durable URLs.
    for row, items in row_items:
        if not all(isinstance(it.get("image_url"), str) and it["image_url"].strip()
                   for it in items):
            continue  # question did not commit — leave the row untouched
        ok = await _rehost_option_urls(session, items, api_base=api_base, http=http)
        if not ok:
            continue
        new_options = dict(row.options or {})
        new_options["items"] = items
        row.options = new_options
        flag_modified(row, "options")
        res.n_committed += 1
        res.n_options_written += len(items)
    await session.flush()
    return res


async def _amain(args) -> int:
    # Import app modules AFTER env is settled (settings load at import time).
    os.environ.setdefault("APP_ENVIRONMENT", "local")
    os.environ.setdefault("LOG_TO_FILE", "false")

    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.services.icons.embedder import raw_embed
    from app.services.icons.relevance_gate import RelevanceGate

    dsn = os.environ.get("PROD_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("error: set PROD_DB_URL (or DATABASE_URL) in env", file=sys.stderr)
        return 2

    if args.slugs:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        slice_map = {s: CURATED_SLICE.get(s, _prettify_slug(s)) for s in slugs}
    else:
        slice_map = dict(CURATED_SLICE)
    if args.limit_packs:
        slice_map = dict(list(slice_map.items())[: args.limit_packs])

    images_cfg = settings.images
    gate_cfg = images_cfg.relevance_gate
    gate = RelevanceGate(
        embed_fn=raw_embed,
        query_prefix=getattr(images_cfg, "query_prefix", ""),
        margin=float(gate_cfg.margin),
        concrete_floor=float(gate_cfg.concrete_floor),
        question_min_fraction=float(gate_cfg.question_min_fraction),
    )

    connect_args = {"ssl": True} if "sqlite" not in dsn else {}
    engine = create_async_engine(_normalize_dsn(dsn), connect_args=connect_args)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    budget_micros = int(round(float(args.budget_usd) * 100_000))
    run_state = {"run_spent_micros": 0, "image_size": args.image_size}
    results: list[PackResult] = []

    try:
        async with httpx.AsyncClient() as http:
            for slug, universe in slice_map.items():
                if run_state["run_spent_micros"] >= budget_micros:
                    print(
                        f"RUN BUDGET REACHED (${run_state['run_spent_micros'] / 100_000.0:.4f}"
                        f" >= ${args.budget_usd}); stopping before {slug}",
                        flush=True,
                    )
                    break
                print(f"== pack {slug} (universe: {universe}) ==", flush=True)
                async with session_factory() as session:
                    try:
                        res = await _process_pack(
                            session, slug=slug, universe=universe,
                            api_base=args.api_base, dry_run=args.dry_run,
                            gate=gate, images_cfg=images_cfg, http=http,
                            run_state=run_state,
                        )
                        if args.dry_run:
                            await session.rollback()
                        else:
                            await session.commit()
                    except Exception as exc:  # noqa: BLE001 — isolate pack failures
                        await session.rollback()
                        res = PackResult(slug=slug, universe=universe,
                                         error=f"{type(exc).__name__}: {exc}")
                results.append(res)
                print("  " + json.dumps(res.as_dict()), flush=True)
    finally:
        await engine.dispose()

    total_cost = sum(r.cost_usd for r in results)
    summary = {
        "packs": [r.as_dict() for r in results],
        "totals": {
            "packs_processed": len(results),
            "questions": sum(r.n_questions for r in results),
            "questions_cleared_gate": sum(r.n_cleared for r in results),
            "questions_committed": sum(r.n_committed for r in results),
            "options_written": sum(r.n_options_written for r in results),
            "images_generated": sum(r.generated for r in results),
            "images_reused": sum(r.reused for r in results),
            "run_cost_usd": round(total_cost, 4),
        },
        "dry_run": bool(args.dry_run),
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary["totals"], indent=2))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"report written: {args.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slugs", help="comma-separated topic slugs (default: curated slice)")
    parser.add_argument("--limit-packs", type=int, default=None)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE,
                        help="public API origin used to build durable /api/v1/media URLs")
    parser.add_argument("--budget-usd", type=float, default=8.0,
                        help="per-RUN spend stop (the lifetime ledger cap still applies)")
    parser.add_argument("--image-size", type=int, default=512,
                        help="square render size for answer images")
    parser.add_argument("--dry-run", action="store_true",
                        help="gate-only preview: no FAL calls, no writes")
    parser.add_argument("--report", help="write a JSON report to this path")
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
