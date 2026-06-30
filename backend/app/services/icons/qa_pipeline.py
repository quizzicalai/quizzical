"""Same-universe Q&A image generation (PRIORITY 2 — DRAFT, flag-gated OFF).

Topic + Q&A string -> a universe-consistent image (e.g. Harry Potter ->
"Dumbledore looking into a pensieve"), generated with the existing FAL client
(``app.services.image_service``) + topic-aware prompts
(``app.agent.tools.image_tools.build_qa_image_prompt``), and bound ADDITIVELY
into the build artefact as ``image_url`` / ``image_alt`` on each question/option.

Runs at PRECOMPUTE/build time only (never per live request), so the FE renders a
cached, content-addressed image with zero added quiz latency. Generic icons stay
the fallback for strings this path skips or that fall to unknown topics.

Hard rules honoured here:
  * EVERY FAL call goes through ``FalLedger.guarded_generate`` — the lifetime
    $-cap is checked before, and spend recorded after, with no exception.
  * Dedup: an identical ``(prompt, provider, model)`` already in ``media_assets``
    is REUSED (no FAL spend), and a 'reused' audit row is written to the ledger.
  * Fail-open / fail-quiet: any error binds NO image for that string and never
    breaks the build. When the cap is exhausted, remaining strings simply get no
    generated image (and can still fall back to a generic icon).

This module is imported ONLY on the flag-ON generation path (lazily, from the
hook), so flag-off builds never construct a FAL client or load this code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


@dataclass
class QaGenStats:
    """Per-build summary, surfaced for the cost/eval report + structured logs."""

    generated: int = 0
    reused: int = 0
    blocked: int = 0
    skipped: int = 0
    # Strings the relevance gate routed AWAY from FAL (fell back to icons). These
    # are the budget the gate SAVED — surfaced so the cost win is observable.
    gated_out: int = 0
    cost_cents: int = 0
    examples: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "reused": self.reused,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "gated_out": self.gated_out,
            "cost_usd": round(self.cost_cents / 100.0, 4),
            "n_examples": len(self.examples),
        }


def _topic_name(artefact: Any) -> str:
    """Best-effort universe name from the pack artefact (tolerant of shape)."""
    if not isinstance(artefact, dict):
        return ""
    topic = artefact.get("topic")
    if isinstance(topic, dict):
        name = topic.get("display_name") or topic.get("slug")
        if isinstance(name, str) and name.strip():
            return name.strip()
    for key in ("category", "topic", "display_name"):
        val = artefact.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _topic_slug(artefact: Any) -> str | None:
    if isinstance(artefact, dict):
        topic = artefact.get("topic")
        if isinstance(topic, dict):
            slug = topic.get("slug")
            if isinstance(slug, str) and slug.strip():
                return slug.strip()
    return None


class QaImageGenerator:
    """Generate + bind same-universe images for a build artefact's Q&A.

    Construct one per build run with the live session, the FAL ledger, the FAL
    client, and ``settings.image_gen``. ``enrich(artefact)`` mutates the artefact
    in place (additive only) and returns ``QaGenStats``.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        ledger,  # FalLedger
        client,  # FalImageClient
        image_gen_cfg,  # settings.image_gen
        gate=None,  # RelevanceGate | None
        max_images: int | None = None,
    ) -> None:
        self.session = session
        self.ledger = ledger
        self.client = client
        self.cfg = image_gen_cfg
        # Per-string relevance gate. None => attempt every string (legacy). When
        # set, abstract/non-depictable strings are routed away from FAL and fall
        # back to the $0 generic-icon binder — the budget-saving guardrail.
        self.gate = gate
        # Optional per-build ceiling on number of generated images (defence in
        # depth on top of the $-cap; None = only the $-cap limits us).
        self.max_images = max_images

    async def enrich(self, artefact: Any) -> QaGenStats:
        stats = QaGenStats()
        if not isinstance(artefact, dict):
            return stats
        questions = artefact.get("questions")
        if not isinstance(questions, list):
            return stats

        topic = _topic_name(artefact)
        slug = _topic_slug(artefact)
        if not topic:
            # No universe to anchor on -> nothing same-universe to do.
            logger.info("qa_image.enrich.no_topic")
            return stats

        for q in questions:
            if not isinstance(q, dict):
                continue
            stem = q.get("question_text") or q.get("text") or q.get("question")
            await self._enrich_target(q, topic, slug, stem, "question", stats)
            options = q.get("options")
            if isinstance(options, list):
                for opt in options:
                    if isinstance(opt, dict):
                        await self._enrich_target(
                            opt, topic, slug, opt.get("text"), "answer", stats
                        )

        logger.info("qa_image.enrich.done", topic=topic, **stats.as_dict())
        return stats

    async def _enrich_target(  # noqa: C901 — linear guard→dedup→generate flow; branches are inherent
        self,
        target: dict,
        topic: str,
        slug: str | None,
        text: Any,
        kind: str,
        stats: QaGenStats,
    ) -> None:
        # Idempotent / re-run safe: never overwrite an existing image.
        if target.get("image_url"):
            return
        if not (isinstance(text, str) and text.strip()):
            return
        if self.max_images is not None and stats.generated >= self.max_images:
            stats.skipped += 1
            return

        # RELEVANCE GATE — the make-or-break guardrail. Abstract / non-depictable
        # strings are routed AWAY from FAL (they fall back to the $0 generic-icon
        # binder), so budget is spent only on concrete, universe-anchored strings
        # that yield a logical same-universe image. Runs BEFORE the (cheap) prompt
        # build and well before any FAL call. Fail-safe: a gate error => skip.
        if self.gate is not None:
            decision = await self.gate.score(text)
            if not decision.generate:
                stats.gated_out += 1
                logger.debug(
                    "qa_image.gated_out",
                    kind=kind,
                    reason=decision.reason,
                    concrete_sim=decision.concrete_sim,
                    abstract_sim=decision.abstract_sim,
                )
                return

        # Lazy imports keep this module cheap to import.
        from app.agent.tools.image_tools import (  # noqa: PLC0415
            build_qa_image_prompt,
            derive_seed,
        )
        from app.services.precompute.dedup import (  # noqa: PLC0415
            find_media_asset_by_prompt_hash,
            prompt_hash,
        )

        try:
            built = build_qa_image_prompt(
                topic=topic,
                text=text,
                kind=kind,
                style_suffix=getattr(self.cfg, "style_suffix", ""),
                negative_prompt=getattr(self.cfg, "negative_prompt", ""),
            )
        except Exception:  # noqa: BLE001 — never break a build over prompt build
            logger.warning("qa_image.prompt_failed", exc_info=True)
            stats.skipped += 1
            return

        prompt = built["prompt"]
        provider = getattr(self.cfg, "provider", "fal")
        model = getattr(self.cfg, "model", "")
        phash = prompt_hash(prompt, provider=provider, model=model)

        # 1) Dedup — reuse an identical prior asset, $0, no FAL call.
        try:
            existing = await find_media_asset_by_prompt_hash(
                self.session, prompt=prompt, provider=provider, model=model
            )
        except Exception:  # noqa: BLE001 — dedup is best-effort
            existing = None
        if existing is not None and getattr(existing, "storage_uri", None):
            self._attach(target, existing.storage_uri, topic, text)
            stats.reused += 1
            await self.ledger.record(
                purpose="qa_image", cost_cents=0, status="reused",
                topic_slug=slug, prompt_hash=phash,
                fal_request_url=existing.storage_uri,
            )
            return

        # 2) Generate through the ledger guard (cap-checked + recorded).
        seed = derive_seed(slug or topic, text)

        async def _gen() -> str | None:
            return await self.client.generate(
                prompt=prompt,
                negative_prompt=built.get("negative_prompt") or None,
                seed=seed,
            )

        spent_before = stats.cost_cents
        snap_before = await self.ledger.snapshot()
        url = await self.ledger.guarded_generate(
            _gen, purpose="qa_image", topic_slug=slug, prompt_hash=phash
        )
        snap_after = await self.ledger.snapshot()
        delta = max(0, snap_after.spent_cents - snap_before.spent_cents)
        stats.cost_cents = spent_before + delta

        if url is None:
            # Either the cap blocked it (no spend) or FAL failed open (spend
            # recorded). Distinguish via the spend delta for the stats.
            if delta == 0:
                stats.blocked += 1
            else:
                stats.skipped += 1
            return

        self._attach(target, url, topic, text)
        stats.generated += 1
        if len(stats.examples) < 12:
            stats.examples.append(
                {"kind": kind, "text": text, "prompt": prompt, "image_url": url}
            )

    def _attach(self, target: dict, url: str, topic: str, text: str) -> None:
        from app.agent.tools.image_tools import qa_image_alt  # noqa: PLC0415

        target["image_url"] = url
        target["image_alt"] = qa_image_alt(topic=topic, text=text)
