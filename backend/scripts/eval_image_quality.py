"""Actual-image VISION-JUDGE eval harness — ``scripts/eval_image_quality.py``.

WHY THIS EXISTS (the gap)
-------------------------
``scripts/generate_images_for_packs.py`` (the precompute image judge) scores the
PROMPT + CHARACTER CONCEPT — it explicitly does NOT look at the rendered pixels
(see its ``_build_image_eval_prompt`` docstring: "We evaluate the CONCEPT fit
rather than looking at the image URL directly, since LLMs cannot browse external
image URLs"). That means a deformed face, a blank/placeholder tile, garbled text,
or an off-topic render can pass the concept judge and ship.

This harness closes that gap: it FETCHES each real image and sends the actual
bytes to a VISION-capable model, which scores the rendered pixels for fidelity,
relevance, and on-brand style, and flags hard blockers (deformed_face, off_topic,
placeholder_or_blank, text_garbage, ip_violation). It is READ-ONLY by default and
CI/owner-usable (non-zero exit when pass-rate < ``--min-pass-rate``).

HOW IT COMPLEMENTS THE OTHER EVALS
----------------------------------
* ``generate_images_for_packs.py``  — concept/prompt gate at GENERATION time.
* ``evals/`` (quizzical_evals)       — LLM-as-judge over TEXT artifacts
                                       (synopsis/characters/questions/profile).
* THIS script                        — pixel-level vision judge over the FINAL
                                       rendered images (the missing leg).

Run it after a generation/backfill pass, or periodically over MediaAsset rows, to
catch images that look fine "on paper" but render badly.

VISION MODEL / HOW THE IMAGE IS PASSED
--------------------------------------
The judge call is a DIRECT LiteLLM ``acompletion`` (chat-completions) multimodal
request — the well-trodden vision path that both OpenAI (gpt-4o) and Gemini
(gemini/gemini-flash-latest) accept. We do NOT route through ``llm_service`` here
because that wrapper targets the Responses API and structured TEXT output; a
direct multimodal chat call is simpler, easy to fake in tests, and provider-
agnostic via LiteLLM. The image is delivered as an ``image_url`` content part
holding a base64 ``data:`` URL (fetched bytes) so the model sees the real pixels —
not a URL it would have to (and could not) browse. The judge returns strict JSON.

INPUTS (all three supported)
----------------------------
1. ``--input pairs.json`` — a JSON list of objects:
       {"image_url"|"image_path": ..., "subject": ..., "topic": ...,
        "expected_description"?: ...}
2. ``--media-from-db --since 2026-06-01 --limit 200`` — READ-ONLY query of
   MediaAsset rows (joined to ``characters`` for the depicted subject). Requires
   ``EVAL_DB_URL`` (or ``PROD_DB_URL``) in the environment.
3. ``--dir <folder>`` — a folder of local image files plus a sidecar
   ``subjects.json`` mapping ``{"<filename>": {"subject": ..., "topic": ...}}``.

COST SAFETY
-----------
A fail-safe ``--max-spend`` USD ledger (reuses ``scripts._precompute_spend``).
Before each judge call we check the projected cost; once the cap would be
exceeded we STOP and every remaining image is recorded as ``skipped-budget``
(never silently passed). READ-ONLY: the script never writes images or DB rows
unless ``--write-scores`` is explicitly passed (default OFF), which only updates
``MediaAsset.evaluator_score`` for DB-sourced rows.

VERDICTS
--------
* ``pass``            — fidelity>=7 AND relevance>=7 AND style_ok AND no blockers
* ``fail``            — judged, but missed one of the above
* ``unavailable``     — image could not be fetched/decoded (dead/expired/missing
                        URL, bad path). NOT a silent pass.
* ``skipped-budget``  — ``--max-spend`` reached before this image was judged.
* ``error``           — the vision call itself errored (counts as not-passing).

EXIT CODE
---------
0 when pass-rate (over JUDGEABLE images) >= ``--min-pass-rate`` (default 0.85);
1 otherwise. ``unavailable``/``error`` count against the rate; ``skipped-budget``
images are excluded from the denominator (they were never evaluated).

EXAMPLE COMMANDS
----------------
    # From backend/ — score a hand-built pair list with gpt-4o, $2 cap:
    OPENAI_API_KEY=sk-... python -m scripts.eval_image_quality \
        --input pairs.json --judge-model gpt-4o --max-spend 2.00 \
        --json report.json

    # Score recent DB images (read-only), Gemini judge, CI gate at 0.85:
    EVAL_DB_URL=postgresql://... GEMINI_API_KEY=... \
    python -m scripts.eval_image_quality \
        --media-from-db --since 2026-06-01 --limit 100 \
        --judge-model gemini/gemini-flash-latest --min-pass-rate 0.85

    # Score a local folder + sidecar subjects.json, opt in to persist scores:
    OPENAI_API_KEY=sk-... python -m scripts.eval_image_quality \
        --dir ./out_images --write-scores --max-spend 1.00

KEYS / ACCESS NEEDED TO RUN ON REAL IMAGES
------------------------------------------
* A VISION-capable LLM key: ``OPENAI_API_KEY`` (for ``gpt-4o``) OR
  ``GEMINI_API_KEY`` (for ``gemini/gemini-flash-latest``). LiteLLM reads these
  from the environment.
* Access to REAL generated images — which only exist if the prod FAL key
  (``FAL_KEY`` / ``settings.images``) has been used to generate them. For the
  ``--media-from-db`` mode you additionally need ``EVAL_DB_URL`` (or
  ``PROD_DB_URL``) pointing at a DB that has MediaAsset rows.
* No FAL key is needed BY THIS SCRIPT — it only READS images; it never generates.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Reuse the precompute spend ledger so cost accounting is consistent across the
# image scripts. (Same module ``generate_images_for_packs`` uses.)
from scripts._precompute_spend import COST_LLM_JUDGE_CALL_CENTS, SpendLedger

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_JUDGE_MODEL = "gpt-4o"
"""Multimodal default. ``gemini/gemini-flash-latest`` is a cheaper alternative
both this app and LiteLLM already configure for vision."""

DEFAULT_MIN_PASS_RATE = 0.85
DEFAULT_MAX_SPEND_USD = 5.0
FETCH_TIMEOUT_S = 20
JUDGE_TIMEOUT_S = 60
PASS_FIDELITY = 7
PASS_RELEVANCE = 7
# Conservative projected cost of one vision judge call (cents). A vision call is
# pricier than a text judge (image tokens), so budget ~5x the text-judge cost.
# We both GATE on and CHARGE this amount (via N text-judge units) so the ledger's
# running total reflects true vision spend and the --max-spend cap is honest.
_VISION_JUDGE_UNITS = 5
PROJECTED_JUDGE_CENTS = COST_LLM_JUDGE_CALL_CENTS * _VISION_JUDGE_UNITS  # ~$0.01/image

_KNOWN_BLOCKERS = (
    "deformed_face",
    "off_topic",
    "placeholder_or_blank",
    "text_garbage",
    "ip_violation",
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageItem:
    """One image to evaluate plus the subject/topic it should depict."""

    subject: str
    topic: str = ""
    image_url: str | None = None
    image_path: str | None = None
    expected_description: str | None = None
    # Optional DB identity so ``--write-scores`` can target the right row.
    media_asset_id: str | None = None


@dataclass
class ImageVerdict:
    """Outcome for one image."""

    subject: str
    topic: str
    fidelity: int = 0
    relevance: int = 0
    style_ok: bool = False
    blocking: list[str] = field(default_factory=list)
    verdict: str = "error"  # pass | fail | unavailable | skipped-budget | error
    notes: str = ""
    media_asset_id: str | None = None

    @property
    def judged(self) -> bool:
        """Was this image actually evaluated (counts toward the pass-rate)?"""
        return self.verdict in ("pass", "fail", "unavailable", "error")

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "topic": self.topic,
            "fidelity": self.fidelity,
            "relevance": self.relevance,
            "style_ok": self.style_ok,
            "blocking": list(self.blocking),
            "verdict": self.verdict,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class VisionScore:
    """Structured score returned by the vision judge."""

    fidelity: int
    relevance: int
    style_ok: bool
    blocking_reasons: list[str]
    notes: str = ""


# ---------------------------------------------------------------------------
# Vision client seam (real LiteLLM; fakeable in tests)
# ---------------------------------------------------------------------------


class VisionClient(Protocol):
    async def score(
        self,
        *,
        model: str,
        subject: str,
        topic: str,
        expected_description: str | None,
        image_data_url: str,
        timeout_s: int,
    ) -> VisionScore: ...


_VISION_SYSTEM = (
    "You are an exacting art director QA-ing AI-generated portrait/scene images "
    "for a personality-quiz app. You are shown the ACTUAL rendered image. Judge "
    "the PIXELS, not a description. Be calibrated; do not pass deformed, blurry, "
    "blank, or off-topic images."
)


def _build_vision_user_prompt(
    *, subject: str, topic: str, expected_description: str | None
) -> str:
    exp = (
        f"\nExpected (ground truth, if any): {expected_description}"
        if expected_description
        else ""
    )
    blockers = ", ".join(_KNOWN_BLOCKERS)
    return (
        "Evaluate the attached image.\n"
        f"Subject it should depict: {subject}\n"
        f"Topic / universe: {topic or '(unspecified)'}{exp}\n\n"
        "Score on these axes and return ONLY a JSON object:\n"
        '  "fidelity": int 1-10  '
        "(clean, well-rendered image — NOT deformed/blurry/artifacted; 10=flawless)\n"
        '  "relevance": int 1-10  '
        "(does the image actually match the subject & topic? 10=spot on)\n"
        '  "style_ok": bool  '
        "(on-brand: a single coherent illustrated portrait, consistent palette)\n"
        '  "blocking_reasons": string[]  '
        f"(zero or more of: {blockers}; [] if none)\n"
        '  "notes": string  (<= 30 words; what you saw)\n\n'
        "Do NOT flag branded/trademarked characters as IP unless the image is a "
        "verbatim copyrighted logo/frame; we intentionally pass branded "
        "characters. Reserve 'ip_violation' for blatant cases only.\n"
        "Return ONLY the JSON object, no prose."
    )


class LiteLLMVisionClient:
    """Real vision judge via a DIRECT LiteLLM ``acompletion`` multimodal call.

    Constructed only on a real run. The image is sent as an ``image_url`` content
    part holding a base64 ``data:`` URL, which is the multimodal format both
    OpenAI (gpt-4o) and Gemini accept through LiteLLM.
    """

    def __init__(self) -> None:
        import litellm  # local import keeps the fake/test path dependency-light

        litellm.suppress_debug_info = True
        litellm.drop_params = True
        self._litellm = litellm

    async def score(
        self,
        *,
        model: str,
        subject: str,
        topic: str,
        expected_description: str | None,
        image_data_url: str,
        timeout_s: int,
    ) -> VisionScore:  # pragma: no cover - network path
        user_text = _build_vision_user_prompt(
            subject=subject, topic=topic, expected_description=expected_description
        )
        messages = [
            {"role": "system", "content": _VISION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
        resp = await self._litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
            timeout=timeout_s,
            response_format={"type": "json_object"},
        )
        text = _extract_text(resp)
        return parse_vision_score(text)


def _extract_text(resp: Any) -> str:  # pragma: no cover - network path
    """Pull the assistant text out of a LiteLLM chat-completions response."""
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        msg = (
            first.get("message")
            if isinstance(first, dict)
            else getattr(first, "message", None)
        )
        content = (
            msg.get("content")
            if isinstance(msg, dict)
            else getattr(msg, "content", None)
        )
        if isinstance(content, str):
            return content
    return ""


def parse_vision_score(text: str) -> VisionScore:
    """Parse a (possibly fenced) JSON judge response into a VisionScore.

    Tolerant: clamps scores to 1-10, coerces booleans, keeps only known blockers,
    and degrades a garbage response to a clearly-failing score (so a malformed
    judge reply never silently passes).
    """
    raw = (text or "").strip()
    # Strip ```json fences if present.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rstrip("`").rstrip()
    # Grab the first {...} block.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    obj: dict[str, Any] = {}
    if m:
        try:
            obj = json.loads(m.group(0))
        except Exception:
            obj = {}

    def _int(key: str) -> int:
        try:
            return max(1, min(10, int(round(float(obj.get(key, 0))))))
        except Exception:
            return 1

    blockers_raw = obj.get("blocking_reasons") or []
    if not isinstance(blockers_raw, list):
        blockers_raw = [str(blockers_raw)]
    blockers = [str(b).strip() for b in blockers_raw if str(b).strip()]

    if not obj:
        # Unparseable judge output -> explicit failing score, not a pass.
        return VisionScore(
            fidelity=1,
            relevance=1,
            style_ok=False,
            blocking_reasons=["unparseable_judge_output"],
            notes=raw[:200],
        )

    return VisionScore(
        fidelity=_int("fidelity"),
        relevance=_int("relevance"),
        style_ok=bool(obj.get("style_ok", False)),
        blocking_reasons=blockers,
        notes=str(obj.get("notes", ""))[:300],
    )


def verdict_from_score(score: VisionScore) -> str:
    """Apply the pass rule: fidelity>=7 AND relevance>=7 AND style_ok AND no
    blocking_reasons."""
    passed = (
        score.fidelity >= PASS_FIDELITY
        and score.relevance >= PASS_RELEVANCE
        and score.style_ok
        and not score.blocking_reasons
    )
    return "pass" if passed else "fail"


# ---------------------------------------------------------------------------
# Image fetching (URL / local path / data URL) -> base64 data URL
# ---------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:image/[\w.+-]+;base64,", re.IGNORECASE)


def _ext_to_mime(suffix: str) -> str:
    s = suffix.lower().lstrip(".")
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(s, "image/jpeg")


async def fetch_image_data_url(
    item: ImageItem, *, timeout_s: int, http_client: Any | None = None
) -> str | None:
    """Resolve an image to a base64 ``data:`` URL, or None if unavailable.

    Handles: already-a-data-URL, local ``image_path``, and ``image_url`` (http(s)
    fetched via httpx, OR a ``data:``/``file`` URL, OR a local path supplied as a
    URL). A network/HTTP/decoding failure returns None (-> ``unavailable``).
    """
    # 1) Explicit local path.
    if item.image_path:
        return _read_local_image(item.image_path)

    url = (item.image_url or "").strip()
    if not url:
        return None

    # 2) Already a data URL.
    if _DATA_URL_RE.match(url):
        return url

    # 3) A local path handed in via the URL field.
    if not url.lower().startswith(("http://", "https://")):
        return _read_local_image(url)

    # 4) Remote fetch via httpx.
    try:
        if http_client is not None:
            resp = await http_client.get(url, timeout=timeout_s)
        else:  # pragma: no cover - exercised only on real runs
            import httpx

            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(url, timeout=timeout_s)
        status = getattr(resp, "status_code", 0)
        if status != 200:
            return None
        content = resp.content
        if not content:
            return None
        ctype = ""
        headers = getattr(resp, "headers", {}) or {}
        try:
            ctype = headers.get("content-type", "") or headers.get("Content-Type", "")
        except Exception:
            ctype = ""
        mime = ctype.split(";")[0].strip() if ctype.startswith("image/") else "image/jpeg"
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def _read_local_image(path: str) -> str | None:
    try:
        p = Path(path)
        data = p.read_bytes()
        if not data:
            return None
        mime = _ext_to_mime(p.suffix)
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core evaluation loop
# ---------------------------------------------------------------------------


async def evaluate_images(
    items: list[ImageItem],
    *,
    vision_client: VisionClient,
    judge_model: str,
    spend_ledger: SpendLedger,
    http_client: Any | None = None,
    fetch_timeout_s: int = FETCH_TIMEOUT_S,
    judge_timeout_s: int = JUDGE_TIMEOUT_S,
) -> list[ImageVerdict]:
    """Fetch + judge each image, honouring the spend cap (fail-safe).

    Pure/injectable: ``vision_client`` and ``http_client`` are passed in so tests
    use fakes with NO network. Returns one ImageVerdict per input item, in order.
    """
    verdicts: list[ImageVerdict] = []
    budget_exhausted = False

    for item in items:
        v = ImageVerdict(
            subject=item.subject,
            topic=item.topic,
            media_asset_id=item.media_asset_id,
        )

        # Fail-safe budget gate FIRST: once the cap would be exceeded, every
        # remaining image is recorded as skipped-budget (never silently passed).
        if budget_exhausted or spend_ledger.would_exceed(PROJECTED_JUDGE_CENTS):
            budget_exhausted = True
            v.verdict = "skipped-budget"
            v.notes = "max-spend reached before this image"
            verdicts.append(v)
            continue

        data_url = await fetch_image_data_url(
            item, timeout_s=fetch_timeout_s, http_client=http_client
        )
        if data_url is None:
            v.verdict = "unavailable"
            v.notes = "image could not be fetched/decoded"
            verdicts.append(v)
            continue

        try:
            score = await asyncio.wait_for(
                vision_client.score(
                    model=judge_model,
                    subject=item.subject,
                    topic=item.topic,
                    expected_description=item.expected_description,
                    image_data_url=data_url,
                    timeout_s=judge_timeout_s,
                ),
                timeout=judge_timeout_s + 5,
            )
            # Only charge once we actually made the (paid) judge call. Charge the
            # full projected vision cost (N text-judge units) so the cap is honest.
            spend_ledger.charge_llm_judge(_VISION_JUDGE_UNITS)
            v.fidelity = score.fidelity
            v.relevance = score.relevance
            v.style_ok = score.style_ok
            v.blocking = list(score.blocking_reasons)
            v.notes = score.notes
            v.verdict = verdict_from_score(score)
        except Exception as exc:  # judge call failed -> not a pass.
            spend_ledger.charge_llm_judge(_VISION_JUDGE_UNITS)
            v.verdict = "error"
            v.notes = f"judge error: {str(exc)[:160]}"

        verdicts.append(v)

    return verdicts


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------


def aggregate(
    verdicts: list[ImageVerdict], spend_ledger: SpendLedger
) -> dict[str, Any]:
    judged = [v for v in verdicts if v.judged]
    passed = [v for v in judged if v.verdict == "pass"]
    pass_rate = (len(passed) / len(judged)) if judged else 0.0

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1

    blockers: dict[str, int] = {}
    for v in verdicts:
        for b in v.blocking:
            blockers[b] = blockers.get(b, 0) + 1

    # Score distribution buckets over judged-with-scores images.
    scored = [v for v in judged if v.verdict in ("pass", "fail")]
    fidelity_dist = _bucket([v.fidelity for v in scored])
    relevance_dist = _bucket([v.relevance for v in scored])

    return {
        "total": len(verdicts),
        "judged": len(judged),
        "passed": len(passed),
        "pass_rate": round(pass_rate, 4),
        "verdict_counts": counts,
        "blocking_reason_counts": blockers,
        "fidelity_distribution": fidelity_dist,
        "relevance_distribution": relevance_dist,
        "spend": spend_ledger.snapshot(),
    }


def _bucket(values: list[int]) -> dict[str, int]:
    out = {"1-3": 0, "4-6": 0, "7-8": 0, "9-10": 0}
    for v in values:
        if v <= 3:
            out["1-3"] += 1
        elif v <= 6:
            out["4-6"] += 1
        elif v <= 8:
            out["7-8"] += 1
        else:
            out["9-10"] += 1
    return out


def render_table(verdicts: list[ImageVerdict], agg: dict[str, Any]) -> str:
    lines: list[str] = []
    header = f"{'subject':28s} {'topic':18s} {'fid':>3s} {'rel':>3s} {'sty':>3s} {'verdict':14s} blocking"
    lines.append(header)
    lines.append("-" * len(header))
    for v in verdicts:
        sty = "yes" if v.style_ok else "no"
        block = ",".join(v.blocking) if v.blocking else "-"
        lines.append(
            f"{_trunc(v.subject, 28):28s} {_trunc(v.topic, 18):18s} "
            f"{v.fidelity:>3d} {v.relevance:>3d} {sty:>3s} "
            f"{v.verdict:14s} {block}"
        )
    lines.append("")
    lines.append(
        f"pass_rate={agg['pass_rate']:.2%}  "
        f"passed={agg['passed']}/{agg['judged']} judged  "
        f"(total={agg['total']})"
    )
    lines.append(f"verdicts: {agg['verdict_counts']}")
    if agg["blocking_reason_counts"]:
        lines.append(f"blocking: {agg['blocking_reason_counts']}")
    lines.append(
        f"fidelity dist: {agg['fidelity_distribution']}  "
        f"relevance dist: {agg['relevance_distribution']}"
    )
    spend = agg["spend"]
    lines.append(f"spend: ${spend['spent_usd']:.4f} / cap ${spend['cap_usd']:.2f}")
    return "\n".join(lines)


def _trunc(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------


def load_pairs(path: Path) -> list[ImageItem]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("--input JSON must be a list (or {'items': [...]})")
    items: list[ImageItem] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        items.append(
            ImageItem(
                subject=str(row.get("subject", "")).strip() or "(unknown)",
                topic=str(row.get("topic", "")).strip(),
                image_url=row.get("image_url"),
                image_path=row.get("image_path"),
                expected_description=row.get("expected_description"),
            )
        )
    return items


def load_dir(folder: Path) -> list[ImageItem]:
    """Folder of images + sidecar ``subjects.json`` mapping filename -> metadata.

    Sidecar shape:
        {"hero.png": {"subject": "Hero", "topic": "Demo", "expected_description": ...}}
    Images without a sidecar entry use the filename stem as the subject.
    """
    sidecar = folder / "subjects.json"
    mapping: dict[str, dict[str, Any]] = {}
    if sidecar.exists():
        try:
            loaded = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                mapping = loaded
        except Exception:
            mapping = {}

    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    items: list[ImageItem] = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in exts:
            continue
        meta = mapping.get(p.name) or mapping.get(p.stem) or {}
        items.append(
            ImageItem(
                subject=str(meta.get("subject", p.stem)).strip() or p.stem,
                topic=str(meta.get("topic", "")).strip(),
                image_path=str(p),
                expected_description=meta.get("expected_description"),
            )
        )
    return items


def _normalize_dsn(raw: str) -> str:
    """asyncpg needs ssl via ``connect_args``; mirror audit_pack_image_coverage."""
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


async def load_media_from_db(
    *, since: str | None, limit: int, db_url: str
) -> list[ImageItem]:
    """READ-ONLY load of MediaAsset rows joined to the depicting character.

    Joins ``characters.image_asset_id -> media_assets.id`` so each image carries
    the subject (character name) and topic context. Falls back to the asset alone
    (subject '(unknown)') when no character references it. Never writes.
    """
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    dsn = _normalize_dsn(db_url)
    connect_args = {"ssl": True} if "+asyncpg" in dsn else {}
    engine = create_async_engine(dsn, connect_args=connect_args)
    items: list[ImageItem] = []
    try:
        clauses = ["m.storage_uri IS NOT NULL", "m.storage_uri <> ''"]
        params: dict[str, Any] = {"lim": int(limit)}
        if since:
            clauses.append("m.created_at >= :since")
            params["since"] = since
        where = " AND ".join(clauses)
        query = text(
            "SELECT m.id::text AS media_id, m.storage_uri AS uri, "
            "       c.name AS subject "
            "FROM media_assets m "
            "LEFT JOIN characters c ON c.image_asset_id = m.id "
            f"WHERE {where} "
            "ORDER BY m.created_at DESC "
            "LIMIT :lim"
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(query, params)).mappings().all()
        for r in rows:
            items.append(
                ImageItem(
                    subject=(r.get("subject") or "(unknown)"),
                    topic="",
                    image_url=r.get("uri"),
                    media_asset_id=r.get("media_id"),
                )
            )
    finally:
        await engine.dispose()
    return items


async def write_scores_to_db(
    verdicts: list[ImageVerdict], *, db_url: str
) -> int:
    """OPT-IN: persist ``fidelity`` to ``MediaAsset.evaluator_score`` (1-10).

    Only updates rows that were judged (verdict pass/fail) and carry a
    ``media_asset_id``. Returns the number of rows updated. Never called unless
    ``--write-scores`` is passed.
    """
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    targets = [
        v
        for v in verdicts
        if v.media_asset_id and v.verdict in ("pass", "fail")
    ]
    if not targets:
        return 0

    dsn = _normalize_dsn(db_url)
    connect_args = {"ssl": True} if "+asyncpg" in dsn else {}
    engine = create_async_engine(dsn, connect_args=connect_args)
    updated = 0
    try:
        async with engine.begin() as conn:
            for v in targets:
                await conn.execute(
                    text(
                        "UPDATE media_assets SET evaluator_score = :s "
                        "WHERE id = :id"
                    ),
                    {"s": int(v.fidelity), "id": v.media_asset_id},
                )
                updated += 1
    finally:
        await engine.dispose()
    return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path, help="pairs.json list of image+subject")
    src.add_argument(
        "--media-from-db",
        action="store_true",
        help="read MediaAsset rows (READ-ONLY); needs EVAL_DB_URL/PROD_DB_URL",
    )
    src.add_argument("--dir", type=Path, help="folder of local images + subjects.json")

    p.add_argument("--since", default=None, help="(db) ISO date floor on created_at")
    p.add_argument("--limit", type=int, default=100, help="(db) max rows (default 100)")

    p.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"vision-capable model (default {DEFAULT_JUDGE_MODEL}); "
        "e.g. gemini/gemini-flash-latest",
    )
    p.add_argument(
        "--max-spend",
        type=float,
        default=DEFAULT_MAX_SPEND_USD,
        help=f"hard USD spend cap (default {DEFAULT_MAX_SPEND_USD}); 0 disables",
    )
    p.add_argument(
        "--min-pass-rate",
        type=float,
        default=DEFAULT_MIN_PASS_RATE,
        help=f"CI gate; exit 1 below this (default {DEFAULT_MIN_PASS_RATE})",
    )
    p.add_argument("--json", type=Path, default=None, help="write JSON report here")
    p.add_argument(
        "--write-scores",
        action="store_true",
        help="OPT-IN: persist fidelity to MediaAsset.evaluator_score (db rows only)",
    )
    p.add_argument(
        "--fetch-timeout-s", type=int, default=FETCH_TIMEOUT_S, help="image fetch timeout"
    )
    return p


def _resolve_db_url() -> str | None:
    import os

    return os.environ.get("EVAL_DB_URL") or os.environ.get("PROD_DB_URL")


async def _load_items(args: argparse.Namespace) -> list[ImageItem]:
    if args.input:
        return load_pairs(args.input)
    if args.dir:
        return load_dir(args.dir)
    # --media-from-db
    db_url = _resolve_db_url()
    if not db_url:
        raise SystemExit(
            "error: --media-from-db requires EVAL_DB_URL or PROD_DB_URL in env"
        )
    return await load_media_from_db(
        since=args.since, limit=args.limit, db_url=db_url
    )


async def _run_async(args: argparse.Namespace) -> int:
    items = await _load_items(args)
    if not items:
        print("No images to evaluate.", file=sys.stderr)
        return 1

    spend_ledger = SpendLedger(cap_cents=int(round(args.max_spend * 100)))
    vision_client: VisionClient = LiteLLMVisionClient()

    verdicts = await evaluate_images(
        items,
        vision_client=vision_client,
        judge_model=args.judge_model,
        spend_ledger=spend_ledger,
        fetch_timeout_s=args.fetch_timeout_s,
    )

    agg = aggregate(verdicts, spend_ledger)
    print(render_table(verdicts, agg))

    # OPT-IN persistence (default OFF -> read-only).
    if args.write_scores:
        db_url = _resolve_db_url()
        if not db_url:
            print(
                "warning: --write-scores ignored (no EVAL_DB_URL/PROD_DB_URL)",
                file=sys.stderr,
            )
        else:
            n = await write_scores_to_db(verdicts, db_url=db_url)
            print(f"--write-scores: updated {n} MediaAsset row(s)", file=sys.stderr)

    report = {
        "judge_model": args.judge_model,
        "min_pass_rate": args.min_pass_rate,
        "results": [v.as_dict() for v in verdicts],
        "aggregate": agg,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Wrote JSON report -> {args.json}", file=sys.stderr)

    # CI gate: non-zero exit when pass-rate below the floor (only meaningful if
    # something was actually judged).
    if agg["judged"] == 0:
        print("No images were judged (all unavailable/skipped).", file=sys.stderr)
        return 1
    return 0 if agg["pass_rate"] >= args.min_pass_rate else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
