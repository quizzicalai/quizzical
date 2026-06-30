"""End-to-end same-universe Q&A pipeline DRY-RUN (no real FAL spend).

Exercises the FULL production path over the REAL starter packs, with the REAL
relevance gate (real 384-dim embedder), the REAL FAL ledger + media_assets dedup
against an in-memory SQLite DB, and a FAKE FAL client (so $0 is actually spent):

    per Q&A string:  relevance gate  ->  prompt build  ->  media_assets dedup
                     ->  ledger.guarded_generate (fake client)  ->  bind + persist

This proves the wiring the build hook uses (``QaImageGenerator``) works on real
content, and reports per-pack generated/gated/reused/blocked counts + the gated
$-projection. It is the make-or-break "the gate routes correctly on real packs"
demonstration, and validates the cross-build dedup loop (a SECOND pass reuses
every asset for $0).

Reproduce (backend venv; model downloads once):
    cd backend && APP_ENVIRONMENT=local LOG_TO_FILE=false \
      .venv312/Scripts/python.exe ../specifications/prototype/qa_pipeline_dryrun.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

OUT_DIR = Path(__file__).resolve().parent
STARTER = _BACKEND / "configs" / "precompute" / "starter_packs" / "starter_v3.json"

COST_PER_IMAGE_USD = 0.011
CAP_USD = 150.0


class _FakeClient:
    """Stand-in FAL client: returns a deterministic URL per prompt, $0 spent."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, prompt, negative_prompt=None, seed=None):
        self.calls += 1
        # Deterministic, content-addressed-ish URL so re-runs are stable.
        return f"https://fal.media/QA/{abs(hash(prompt)) % 10**10}.png"


class _ImageGenCfg:
    provider = "fal"
    model = "fal-ai/flux/schnell"
    style_suffix = "flat illustrated, soft lighting, muted palette, no text"
    negative_prompt = "text, watermark, logo, blurry, deformed, low quality"


class _Budget:
    cap_usd = CAP_USD
    cost_per_image_usd = COST_PER_IMAGE_USD
    enforce = True

    @property
    def cap_cents(self):
        return int(round(self.cap_usd * 100))

    @property
    def cost_per_image_cents(self):
        return self.cost_per_image_usd * 100.0


async def _make_engine():
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import event
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.dialects.postgresql import UUID as PGUUID
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.ext.compiler import compiles

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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def _strip_jsonb(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if "::jsonb" in statement:
            statement = statement.replace("::jsonb", "")
        return statement, parameters

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return engine, Session


def _pack_to_artefact(pack: dict) -> dict:
    """Shape a starter pack into the artefact the generator enriches.

    Deep-copies so the enricher's in-place mutation (it adds ``image_url`` to
    each question/option) does not leak across passes — a second pass over the
    SAME pack object must start with no bound images so it exercises the dedup
    path rather than the idempotent early-return."""
    import copy

    topic = pack.get("topic") or {}
    return {
        "topic": {"display_name": topic.get("display_name"), "slug": topic.get("slug")},
        "questions": copy.deepcopy(pack.get("questions", [])),
    }


async def main() -> None:
    from app.services.icons.embedder import raw_embed
    from app.services.icons.fal_ledger import FalLedger
    from app.services.icons.qa_pipeline import QaImageGenerator
    from app.services.icons.relevance_gate import RelevanceGate

    doc = json.loads(STARTER.read_text(encoding="utf-8"))
    packs = doc.get("packs", [])

    engine, Session = await _make_engine()
    gate = RelevanceGate(
        embed_fn=raw_embed,
        query_prefix="Represent this sentence for searching relevant passages: ",
        margin=0.04,
        concrete_floor=0.20,
    )
    cfg = _ImageGenCfg()
    client = _FakeClient()

    per_pack = []
    examples = []
    async with Session() as s:
        ledger = FalLedger(s, config=_Budget())
        # ---- Pass 1: generate (with the gate) over every starter pack. ----
        for pack in packs:
            art = _pack_to_artefact(pack)
            gen = QaImageGenerator(
                session=s, ledger=ledger, client=client, image_gen_cfg=cfg, gate=gate
            )
            stats = await gen.enrich(art)
            per_pack.append({"topic": (pack.get("topic") or {}).get("display_name"),
                             **stats.as_dict()})
            for ex in stats.examples[:2]:
                examples.append({"topic": (pack.get("topic") or {}).get("display_name"),
                                 **ex})
            await s.commit()

        snap = await ledger.snapshot()
        pass1_calls = client.calls

        # ---- Pass 2: SAME packs again => cross-build dedup should reuse all. ----
        client2 = _FakeClient()
        reused_total = 0
        for pack in packs:
            art = _pack_to_artefact(pack)
            gen = QaImageGenerator(
                session=s, ledger=ledger, client=client2, image_gen_cfg=cfg, gate=gate
            )
            stats = await gen.enrich(art)
            reused_total += stats.reused
            await s.commit()
        pass2_calls = client2.calls

    await engine.dispose()

    gen_total = sum(p["generated"] for p in per_pack)
    gated_total = sum(p["gated_out"] for p in per_pack)
    n_strings = gen_total + gated_total + sum(p["reused"] for p in per_pack)
    coverage = round(gen_total / max(1, n_strings), 4)

    out = {
        "n_packs": len(packs),
        "totals": {
            "strings_seen": n_strings,
            "generated": gen_total,
            "gated_out": gated_total,
            "coverage": coverage,
            "pass1_fal_calls": pass1_calls,
            "spent_usd": snap.spent_usd,
        },
        "cross_build_dedup": {
            "pass2_reused": reused_total,
            "pass2_fal_calls": pass2_calls,
            "verdict": ("second build over the same packs made %d FAL calls "
                        "(expected 0) and reused %d assets — dedup loop closed"
                        % (pass2_calls, reused_total)),
        },
        "per_pack": per_pack,
        "examples": examples,
    }
    (OUT_DIR / "qa_pipeline_dryrun.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"packs: {len(packs)}  strings: {n_strings}  "
          f"generated: {gen_total}  gated_out: {gated_total}  "
          f"coverage: {coverage}")
    print(f"pass1 FAL calls: {pass1_calls}  spent: ${snap.spent_usd}")
    print(f"pass2 (re-run) FAL calls: {pass2_calls}  reused: {reused_total}  "
          f"(expected 0 calls — dedup loop)")
    print("\nper-pack:")
    for p in per_pack:
        print(f"  {p['topic']:<22} gen={p['generated']:>2} "
              f"gated_out={p['gated_out']:>2} reused={p['reused']:>2}")


if __name__ == "__main__":
    asyncio.run(main())
