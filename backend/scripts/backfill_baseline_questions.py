"""Generate baseline questions for published packs that shipped with ZERO.

WHY (2026-07-02 audit)
----------------------
24 published packs have no baseline questions: their ``/quiz/start``
short-circuit serves synopsis + cast but every question falls back to the
live agent — slower first question and pointless LLM spend for topics we
already precomputed. This script:

  1. Pulls each zero-question pack's synopsis + character outcomes from PROD
     (read-only).
  2. Generates 5 baseline questions (4 options each) with ``gpt-4o-mini``
     (cheap, structured JSON).
  3. Judge-gates each pack with gemini (score >= 75, no blocking reasons);
     one retry on fail, then the pack is SKIPPED (never ship un-judged
     questions).
  4. Emits a ``build_starter_packs``-compatible source JSON. Shipping is the
     operator's explicit next step (build + sign + POST
     /api/v1/admin/precompute/import) so scope stays controlled.

USAGE (from backend/; PROD_DB_URL + OPENAI_API_KEY + GEMINI_API_KEY in env)
---------------------------------------------------------------------------
    python -m scripts.backfill_baseline_questions \
        --out configs/precompute/starter_packs/zero_question_fix.source.json
    # then:
    #   python -m scripts.build_starter_packs --source ...source.json \
    #       --out ...json --secret-env PRECOMPUTE_HMAC_SECRET
    #   curl -X POST .../admin/precompute/import?force_upgrade=true ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

GEN_MODEL = "gpt-4o-mini"
JUDGE_MODEL = "gemini/gemini-flash-latest"
PASS_SCORE = 75
N_QUESTIONS = 5
N_OPTIONS = 4


def _normalize_dsn(raw: str) -> str:
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


async def _load_zero_question_packs(db_url: str) -> list[dict[str, Any]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_normalize_dsn(db_url), connect_args={"ssl": True})
    try:
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
                        SELECT t.slug, t.display_name, l.version,
                               s.body AS synopsis,
                               cs.composition AS cs_comp,
                               bqs.composition AS bqs_comp
                        FROM latest l
                        JOIN topics t ON t.id = l.topic_id
                        LEFT JOIN synopses s ON s.id = l.synopsis_id
                        LEFT JOIN character_sets cs ON cs.id = l.character_set_id
                        LEFT JOIN baseline_question_sets bqs
                          ON bqs.id = l.baseline_question_set_id
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
    finally:
        await engine.dispose()

    by_id = {c["id"]: c for c in chars}
    packs: list[dict[str, Any]] = []
    for r in rows:
        bqs = r["bqs_comp"]
        if isinstance(bqs, str):
            bqs = json.loads(bqs)
        if (bqs or {}).get("question_ids") or (bqs or {}).get("question_keys"):
            continue
        comp = r["cs_comp"]
        if isinstance(comp, str):
            comp = json.loads(comp)
        cids = [str(c) for c in (comp or {}).get("character_ids", [])]
        cast = [by_id[c] for c in cids if c in by_id]
        if not cast:
            continue  # broken pack (e.g. grimm) — handled separately
        syn = r["synopsis"]
        if isinstance(syn, str):
            syn = json.loads(syn)
        packs.append(
            {
                "slug": r["slug"],
                "display_name": r["display_name"],
                "version": int(r["version"]),
                "synopsis": syn or {},
                "characters": [
                    {
                        "name": c["name"],
                        "short_description": c["short_description"] or "",
                        "profile_text": c["profile_text"] or "",
                        **({"image_url": c["image_url"]} if c["image_url"] else {}),
                    }
                    for c in cast
                ],
            }
        )
    return packs


# ---------------------------------------------------------------------------
# Generation + judging (direct litellm — same pattern as eval_image_quality)
# ---------------------------------------------------------------------------


def _extract_json(text_out: str) -> Any:
    raw = (text_out or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rstrip("`").rstrip()
    m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def _llm_json(model: str, system: str, user: str, *, max_tokens: int) -> Any:
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True
    resp = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
        # Thinking models (gemini flash) count reasoning against max_tokens.
        max_tokens=max_tokens,
        timeout=90,
        response_format={"type": "json_object"},
    )
    choices = getattr(resp, "choices", None) or []
    content = choices[0].message.content if choices else ""
    return _extract_json(content or "")


_GEN_SYSTEM = (
    "You write personality-quiz questions. Given a quiz topic and its outcome "
    "characters, produce fun, specific multiple-choice questions. Each question "
    "is second-person and situational; each option maps to the ENERGY of one "
    "outcome without ever naming any outcome. Keep every scenario inside the "
    "topic's own world and tone (a Star Wars quiz asks about smuggling runs and "
    "cantinas, not office group projects). Across the SET, make sure every "
    "outcome's energy is represented by at least one option somewhere. Options "
    "must be mutually distinct, similar length, and free of letter prefixes. "
    "Return JSON only."
)


def _gen_user_prompt(pack: dict[str, Any]) -> str:
    outcomes = "\n".join(
        f"- {c['name']}: {c['short_description'][:160]}"
        for c in pack["characters"]
    )
    syn = pack["synopsis"] or {}
    return (
        f"Quiz topic: {pack['display_name']}\n"
        f"Synopsis: {str(syn.get('summary', ''))[:400]}\n"
        f"Outcomes:\n{outcomes}\n\n"
        f"Write exactly {N_QUESTIONS} questions, each with exactly {N_OPTIONS} "
        "options. Vary the situations (choices, habits, conflicts, "
        "aesthetics, values). Never name an outcome in a question or option.\n"
        'Return JSON: {"questions": [{"question_text": str, '
        '"options": [str, str, str, str]}, ...]}'
    )


_JUDGE_SYSTEM = (
    "You are a strict editor reviewing personality-quiz questions before "
    "publication. Score the SET 0-100 and list blocking issues (anything that "
    "would embarrass the platform: scenarios that break the topic's theme, "
    "leaking outcome names into questions/options, duplicate/near-duplicate "
    "options or questions, broken grammar). PLATFORM RULE: every question has "
    "exactly 4 options regardless of how many outcomes the quiz has — options "
    "map to outcome ENERGIES, not 1:1 to outcomes, and the scorer blends "
    "answers across the whole set. Do NOT flag option-count-vs-outcome-count "
    "as an issue. Return JSON only."
)


def _judge_user_prompt(pack: dict[str, Any], questions: list[dict[str, Any]]) -> str:
    return (
        f"Quiz topic: {pack['display_name']}\n"
        f"Outcomes: {', '.join(c['name'] for c in pack['characters'])}\n\n"
        f"Questions:\n{json.dumps(questions, ensure_ascii=False, indent=1)}\n\n"
        'Return JSON: {"score": int 0-100, "blocking_reasons": [str, ...]}'
    )


def _valid_questions(raw: Any) -> list[dict[str, Any]] | None:
    """Deterministic structural gate before the LLM judge."""
    if isinstance(raw, dict):
        raw = raw.get("questions")
    if not isinstance(raw, list) or len(raw) < N_QUESTIONS:
        return None
    out: list[dict[str, Any]] = []
    for q in raw[:N_QUESTIONS]:
        if not isinstance(q, dict):
            return None
        text_v = str(q.get("question_text") or q.get("text") or "").strip()
        opts_raw = q.get("options") or []
        opts = [str(o.get("text") if isinstance(o, dict) else o).strip()
                for o in opts_raw]
        opts = [o for o in opts if o]
        if not text_v or len(opts) != N_OPTIONS or len(set(opts)) != N_OPTIONS:
            return None
        out.append({"question_text": text_v, "options": [{"text": o} for o in opts]})
    return out


async def _questions_for_pack(pack: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Generate + judge, with one retry. None => skip pack (fail-closed)."""
    for attempt in (1, 2):
        raw = await _llm_json(
            GEN_MODEL, _GEN_SYSTEM, _gen_user_prompt(pack), max_tokens=2500
        )
        questions = _valid_questions(raw)
        if questions is None:
            print(f"  [{pack['slug']}] attempt {attempt}: structural fail")
            continue
        verdict = await _llm_json(
            JUDGE_MODEL,
            _JUDGE_SYSTEM,
            _judge_user_prompt(pack, questions),
            max_tokens=2000,
        )
        score = int((verdict or {}).get("score") or 0)
        blocking = list((verdict or {}).get("blocking_reasons") or [])
        if score >= PASS_SCORE and not blocking:
            print(f"  [{pack['slug']}] PASS score={score}")
            return questions
        print(f"  [{pack['slug']}] attempt {attempt}: judge score={score} "
              f"blocking={blocking}")
    return None


async def run(
    *, db_url: str, out_path: Path, limit: int, slugs: set[str] | None = None
) -> int:
    packs = await _load_zero_question_packs(db_url)
    if slugs:
        packs = [p for p in packs if p["slug"] in slugs]
    if limit > 0:
        packs = packs[:limit]
    print(f"zero-question packs with cast: {len(packs)}")
    target_version = max((p["version"] for p in packs), default=3) + 1
    print(f"target pack version: {target_version}")

    topics_out: list[dict[str, Any]] = []
    skipped: list[str] = []
    for pack in packs:
        questions = await _questions_for_pack(pack)
        if questions is None:
            skipped.append(pack["slug"])
            continue
        topics_out.append(
            {
                "slug": pack["slug"],
                "display_name": pack["display_name"],
                "aliases": [],
                "synopsis": pack["synopsis"],
                "characters": pack["characters"],
                "baseline_questions": questions,
            }
        )

    doc = {
        "version": target_version,
        "built_in_env": "starter",
        "description": (
            "2026-07-02 structural fix: baseline questions for published packs "
            "that shipped with zero (gpt-4o-mini gen, gemini judge-gated >= "
            f"{PASS_SCORE})."
        ),
        "topics": topics_out,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {out_path} — {len(topics_out)} packs "
          f"(skipped: {skipped or 'none'})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=0, help="max packs (0=all)")
    p.add_argument("--slugs", default=None,
                   help="comma-separated slug filter (retry stragglers)")
    args = p.parse_args(argv)

    db_url = os.environ.get("PROD_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("error: set PROD_DB_URL (or DATABASE_URL)", file=sys.stderr)
        return 2
    if not os.environ.get("OPENAI_API_KEY"):
        print("error: OPENAI_API_KEY required for gpt-4o-mini", file=sys.stderr)
        return 2
    if not os.environ.get("GEMINI_API_KEY"):
        print("error: GEMINI_API_KEY required for the judge", file=sys.stderr)
        return 2

    slug_set = (
        {s.strip() for s in args.slugs.split(",") if s.strip()}
        if args.slugs
        else None
    )
    return asyncio.run(
        run(db_url=db_url, out_path=args.out, limit=args.limit, slugs=slug_set)
    )


if __name__ == "__main__":
    raise SystemExit(main())
