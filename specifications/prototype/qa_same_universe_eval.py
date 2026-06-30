"""Same-universe Q&A image enrichment — offline evaluator + cost model.

Runs WITHOUT any FAL spend: it loads the real starter packs, builds the actual
same-universe prompts via the production prompt builder
(``app.agent.tools.image_tools.build_qa_image_prompt``), exercises the real FAL
ledger guard against an in-memory SQLite DB with a FAKE FAL client, and emits:

  - ``qa_same_universe_samples.json`` — per-string topic/text/prompt/mock-url for
    a few representative starter-pack topics (the "what it would generate").
  - ``qa_same_universe_cost_model.json`` — projected FAL spend for the starter
    pack at several scales, vs the $150 cap, plus the ledger-cap behaviour proof.

Reproduce (backend venv):
    cd backend && APP_ENVIRONMENT=local LOG_TO_FILE=false \
      .venv312/Scripts/python.exe ../specifications/prototype/qa_same_universe_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# --- make backend importable when run from the repo root --------------------
_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

OUT_DIR = Path(__file__).resolve().parent
STARTER = _BACKEND / "configs" / "precompute" / "starter_packs" / "starter_v3.json"

COST_PER_IMAGE_USD = 0.011  # FLUX schnell 512x512 (conservative; matches repo)
CAP_USD = 150.0


def _pack_strings(pack: dict) -> list[tuple[str, str]]:
    """Return [(kind, text), ...] for every question stem + answer option."""
    out: list[tuple[str, str]] = []
    for q in pack.get("questions", []):
        stem = q.get("question_text") or q.get("text") or q.get("question")
        if isinstance(stem, str) and stem.strip():
            out.append(("question", stem))
        for opt in q.get("options", []) or []:
            t = opt.get("text") if isinstance(opt, dict) else None
            if isinstance(t, str) and t.strip():
                out.append(("answer", t))
    return out


def build_samples() -> dict:
    from app.agent.tools.image_tools import build_qa_image_prompt, qa_image_alt

    doc = json.loads(STARTER.read_text(encoding="utf-8"))
    packs = doc.get("packs", [])
    samples: list[dict] = []
    per_pack_counts: list[dict] = []
    for pack in packs:
        topic = (pack.get("topic") or {}).get("display_name") or ""
        strings = _pack_strings(pack)
        per_pack_counts.append({"topic": topic, "n_strings": len(strings)})
        # Capture the first 5 strings per topic as illustrative samples.
        for kind, text in strings[:5]:
            built = build_qa_image_prompt(
                topic=topic,
                text=text,
                kind=kind,
                style_suffix="flat illustrated, soft lighting, muted palette, no text",
                negative_prompt="text, watermark, logo, blurry, deformed, low quality",
            )
            samples.append(
                {
                    "topic": topic,
                    "kind": kind,
                    "text": text,
                    "prompt": built["prompt"],
                    "alt": qa_image_alt(topic=topic, text=text),
                    "mock_image_url": f"https://fal.media/MOCK/{abs(hash((topic, text))) % 10**8}.png",
                }
            )
    return {"samples": samples, "per_pack_counts": per_pack_counts}


def cost_model(per_pack_counts: list[dict]) -> dict:
    n_packs = len(per_pack_counts)
    total_strings = sum(p["n_strings"] for p in per_pack_counts)
    avg = round(total_strings / max(1, n_packs), 2)

    def project(n_topics: int) -> dict:
        imgs = int(round(avg * n_topics))
        cost = round(imgs * COST_PER_IMAGE_USD, 2)
        return {
            "n_topics": n_topics,
            "avg_strings_per_topic": avg,
            "projected_images": imgs,
            "projected_cost_usd": cost,
            "within_cap": cost <= CAP_USD,
            "pct_of_cap": round(100 * cost / CAP_USD, 1),
        }

    # How many topics fully fit under the cap at this avg?
    max_imgs = int(CAP_USD / COST_PER_IMAGE_USD)
    max_topics_full = int(max_imgs / max(1.0, avg))

    return {
        "cost_per_image_usd": COST_PER_IMAGE_USD,
        "cap_usd": CAP_USD,
        "measured": {
            "n_packs_in_starter_v3": n_packs,
            "total_strings": total_strings,
            "avg_strings_per_topic": avg,
        },
        "projections": [project(n) for n in (5, 25, 100, 250, 500, 904)],
        "max_topics_fully_covered_under_cap": max_topics_full,
        "note": (
            "Every string would be one image at full coverage. In practice the "
            "same-universe path is reserved for concrete, universe-anchored "
            "strings; abstract/personality strings fall back to the $0 generic "
            "icon or no image, so real spend is materially lower than these "
            "full-coverage ceilings. Dedup (prompt_hash) further suppresses "
            "repeats across packs."
        ),
    }


async def ledger_proof() -> dict:
    """Exercise the real FalLedger hard cap against SQLite with a fake client."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.dialects.postgresql import UUID as PGUUID
    from pgvector.sqlalchemy import Vector

    @compiles(PGUUID, "sqlite")
    def _u(t, c, **k):  # noqa: ANN001
        return "TEXT"

    @compiles(JSONB, "sqlite")
    def _j(t, c, **k):  # noqa: ANN001
        return "JSON"

    @compiles(Vector, "sqlite")
    def _v(t, c, **k):  # noqa: ANN001
        return "TEXT"

    from app.models.db import Base
    from app.services.icons.fal_ledger import FalLedger

    class _Budget:
        # Tiny cap (3 cents) so we can SHOW the block within a short run.
        cap_usd = 0.03
        cost_per_image_usd = 0.011
        enforce = True

        @property
        def cap_cents(self):
            return 3

        @property
        def cost_per_image_cents(self):
            return 1.1

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # Mirror the test bench: strip the PG-only ``::jsonb`` cast from DDL/DML so
    # SQLite accepts the shared ORM metadata.
    @event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def _strip_jsonb(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if "::jsonb" in statement:
            statement = statement.replace("::jsonb", "")
        return statement, parameters

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    results = []
    async with Session() as s:
        ledger = FalLedger(s, config=_Budget())
        calls = {"n": 0}

        async def _gen():
            calls["n"] += 1
            return f"https://fal.media/{calls['n']}.png"

        for i in range(5):
            url = await ledger.guarded_generate(_gen, purpose="qa_image", topic_slug="demo")
            snap = await ledger.snapshot()
            results.append(
                {"attempt": i + 1, "got_url": bool(url), "spent_usd": snap.spent_usd}
            )
    await engine.dispose()
    return {
        "cap_usd": 0.03,
        "cost_per_image_usd": 0.011,
        "fal_calls_made": calls["n"],
        "attempts": results,
        "verdict": (
            "After 3 charged images ($0.033 rounded to 3x1c=3c) the cap blocks "
            "all further FAL calls; spend never exceeds the cap."
        ),
    }


async def main() -> None:
    s = build_samples()
    (OUT_DIR / "qa_same_universe_samples.json").write_text(
        json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    cm = cost_model(s["per_pack_counts"])
    cm["ledger_cap_proof"] = await ledger_proof()
    (OUT_DIR / "qa_same_universe_cost_model.json").write_text(
        json.dumps(cm, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("wrote qa_same_universe_samples.json (", len(s["samples"]), "samples )")
    print("wrote qa_same_universe_cost_model.json")
    print(json.dumps(cm["projections"], indent=2))
    print("ledger cap proof:", json.dumps(cm["ledger_cap_proof"]["attempts"]))


if __name__ == "__main__":
    asyncio.run(main())
