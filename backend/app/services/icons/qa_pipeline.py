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

from app.services.icons.fal_ledger import GenerateResult

logger = structlog.get_logger(__name__)


def _fal_generation_enabled() -> bool:
    """True iff the FAL image client would actually make a billable call.

    Mirrors ``image_service._image_gen_enabled`` (``settings.image_gen.enabled``).
    Imported lazily + read defensively so a flag-off build never imports the FAL
    client. When False the pipeline reports ``billed=False`` (no phantom charge)."""
    try:
        from app.services.image_service import _image_gen_enabled  # noqa: PLC0415

        return bool(_image_gen_enabled())
    except Exception:  # noqa: BLE001 — any problem => treat as not-billable (safe)
        return False


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
    # True FAL spend for this build in micro-cents (1 cent = 1000 micros) —
    # lossless, matches the ledger's authoritative unit.
    cost_micros: int = 0
    examples: list[dict[str, str]] = field(default_factory=list)

    @property
    def cost_cents(self) -> float:
        return round(self.cost_micros / 1000.0, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "reused": self.reused,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "gated_out": self.gated_out,
            "cost_usd": round(self.cost_micros / 100_000.0, 4),
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
        style_suffix: str | None = None,  # Q&A-specific scene style; None => cfg
        fal_enabled_fn=None,  # () -> bool; default reads settings.image_gen.enabled
        max_images: int | None = None,
        stem_images: bool = True,
    ) -> None:
        self.session = session
        self.ledger = ledger
        self.client = client
        self.cfg = image_gen_cfg
        # Q&A scenes want a scene-framed style suffix, not the character path's
        # "portrait" one. When provided it overrides ``cfg.style_suffix`` for the
        # Q&A prompt; otherwise we fall back to the image_gen suffix.
        self.style_suffix = style_suffix
        # Predicate: would a FAL call actually bill? (no key / gen disabled =>
        # False => billed=False => no phantom charge). Injectable for tests.
        self._fal_enabled_fn = fal_enabled_fn or _fal_generation_enabled
        # Per-string relevance gate. None => attempt every string (legacy). When
        # set, abstract/non-depictable strings are routed away from FAL and fall
        # back to the $0 generic-icon binder — the budget-saving guardrail.
        self.gate = gate
        # Optional per-build ceiling on number of generated images (defence in
        # depth on top of the $-cap; None = only the $-cap limits us).
        self.max_images = max_images
        # Whether to also generate the best-effort question STEM image after a
        # question's answers commit. The precompute serve path
        # (``hydrator._resolve_baseline_questions``) only surfaces per-OPTION
        # ``image_url`` today — a stem image would never reach the user there —
        # so the pool-builder script disables this to avoid paying for images
        # nothing renders. Default True preserves the build-hook behaviour.
        self.stem_images = stem_images

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

        # Blackbox #5 — STRICT all-or-none PER QUESTION. Each question is handled
        # as a UNIT: bind images for ALL its answers, or NONE (text-only). No more
        # partial coverage (some answers imaged, some not), which read as broken.
        n_questions = 0
        n_questions_imaged = 0
        for q in questions:
            if not isinstance(q, dict):
                continue
            n_questions += 1
            imaged = await self._enrich_question(q, topic, slug, stats)
            if imaged:
                n_questions_imaged += 1

        clear_rate = round(n_questions_imaged / n_questions, 4) if n_questions else 0.0
        logger.info(
            "qa_image.enrich.done",
            topic=topic,
            n_questions=n_questions,
            n_questions_imaged=n_questions_imaged,
            question_clear_rate=clear_rate,
            **stats.as_dict(),
        )
        return stats

    async def _enrich_question(  # noqa: C901 — linear question-level gate → resolve-all → commit-or-none
        self,
        q: dict,
        topic: str,
        slug: str | None,
        stats: QaGenStats,
    ) -> bool:
        """Resolve + bind same-universe images for ONE question, all-or-none.

        Returns True iff the question's answers were imaged (committed). The
        question STEM image is a best-effort bonus bound only on commit; the
        commit gate is on the ANSWER set — every answer must resolve a relevant
        image, else NONE bind (the question stays text-only)."""
        options = q.get("options")
        answers = [o for o in options if isinstance(o, dict)] if isinstance(options, list) else []
        answer_texts = [
            o.get("text") for o in answers
            if isinstance(o.get("text"), str) and o.get("text").strip()
        ]
        if not answers or not answer_texts:
            # Nothing to image as a unit; leave the question untouched.
            return False

        # 1) QUESTION-LEVEL gate — decide ONCE whether this whole question is
        # depictable. Abstract questions fall back to NONE (text-only), counted
        # as gated_out for every target so the budget saving stays observable.
        if self.gate is not None:
            qd = await self.gate.score_question(answer_texts)
            if not qd.generate:
                stats.gated_out += len(answers) + 1  # answers + stem
                logger.debug(
                    "qa_image.question_gated_out",
                    reason=qd.reason,
                    n_answers=qd.n_answers,
                    n_concrete=qd.n_concrete_answers,
                    concrete_fraction=qd.concrete_fraction,
                    mean_margin=qd.mean_margin,
                )
                return False

        # Cap (defence in depth on top of the $-ledger): if a per-build ceiling
        # is set and we've already hit it, don't start a new question.
        if self.max_images is not None and stats.generated >= self.max_images:
            stats.skipped += len(answers) + 1
            return False

        # 2) Resolve an image for EVERY answer (dedup-reuse or ledger-guarded
        # generate). Buffer the results; do NOT mutate the artefact yet.
        staged: list[tuple[dict, str, bool]] = []  # (target, url, was_reused)
        all_answers_resolved = True
        for opt in answers:
            url, reused = await self._resolve_image(
                opt, topic, slug, opt.get("text"), "answer", stats
            )
            if url:
                staged.append((opt, url, reused))
            else:
                all_answers_resolved = False

        if not all_answers_resolved:
            # STRICT all-or-none: at least one answer failed to resolve a relevant
            # image -> bind NONE for this question. Any image we DID generate has
            # already been persisted to media_assets (so the spend is reusable on
            # the next build) and recorded in the ledger; we simply don't attach
            # it. The strings remain free for the $0 generic-icon fallback.
            stats.skipped += len(answers) - len(staged)
            logger.debug(
                "qa_image.question_partial_discarded",
                resolved=len(staged),
                of=len(answers),
            )
            return False

        # 3) COMMIT — every answer resolved. Bind all answer images, plus a
        # best-effort stem image (the question header illustration). ``generated``
        # counts only FRESH generations; reused images were already counted as
        # ``reused`` inside ``_resolve_image`` (so cross-build reuse doesn't
        # double-count as generated).
        for target, url, reused in staged:
            self._attach(target, url, topic, self._target_text(target))
            if not reused:
                stats.generated += 1
            self._maybe_record_example("answer", self._target_text(target), url, stats)

        stem = q.get("question_text") or q.get("text") or q.get("question")
        if self.stem_images and isinstance(stem, str) and stem.strip():
            stem_url, stem_reused = await self._resolve_image(
                q, topic, slug, stem, "question", stats
            )
            if stem_url:
                self._attach(q, stem_url, topic, stem)
                if not stem_reused:
                    stats.generated += 1
                self._maybe_record_example("question", stem, stem_url, stats)

        return True

    @staticmethod
    def _target_text(target: dict) -> str:
        t = target.get("text")
        return t if isinstance(t, str) else ""

    def _maybe_record_example(
        self, kind: str, text: str, url: str, stats: QaGenStats
    ) -> None:
        if len(stats.examples) < 12:
            stats.examples.append({"kind": kind, "text": text, "image_url": url})

    async def _resolve_image(  # noqa: C901 — linear dedup→generate→persist flow; branches are inherent
        self,
        target: dict,
        topic: str,
        slug: str | None,
        text: Any,
        kind: str,
        stats: QaGenStats,
    ) -> tuple[str | None, bool]:
        """Resolve a same-universe image URL for ONE string (dedup-reuse or
        ledger-guarded generate). Returns ``(url_or_None, was_reused)``. Does NOT
        mutate the artefact — the caller (``_enrich_question``) attaches images
        only when the whole question COMMITS (all-or-none, blackbox #5).
        ``was_reused`` lets the caller avoid double-counting a dedup hit as a
        fresh generation.

        A freshly-generated image IS persisted to ``media_assets`` (and recorded
        in the ledger) here regardless of whether the question later commits, so
        the spend is never wasted: the next build dedups + reuses it for $0."""
        # Idempotent / re-run safe: an already-bound image is reused as-is.
        if target.get("image_url"):
            return target["image_url"], True
        if not (isinstance(text, str) and text.strip()):
            return None, False

        # Lazy imports keep this module cheap to import.
        from app.agent.tools.image_tools import (  # noqa: PLC0415
            build_qa_image_prompt,
            derive_seed,
        )
        from app.services.precompute.dedup import (  # noqa: PLC0415
            find_media_asset_by_prompt_hash,
            prompt_hash,
        )

        style_suffix = self.style_suffix
        if not style_suffix:
            style_suffix = getattr(self.cfg, "style_suffix", "")
        try:
            built = build_qa_image_prompt(
                topic=topic,
                text=text,
                kind=kind,
                style_suffix=style_suffix,
                negative_prompt=getattr(self.cfg, "negative_prompt", ""),
            )
        except Exception:  # noqa: BLE001 — never break a build over prompt build
            logger.warning("qa_image.prompt_failed", exc_info=True)
            return None, False

        prompt = built["prompt"]
        provider = getattr(self.cfg, "provider", "fal")
        model = getattr(self.cfg, "model", "")
        image_size = getattr(self.cfg, "image_size", None)
        phash = prompt_hash(prompt, provider=provider, model=model)

        # 1) Dedup — reuse an identical prior asset, $0, no FAL call.
        try:
            existing = await find_media_asset_by_prompt_hash(
                self.session, prompt=prompt, provider=provider, model=model
            )
        except Exception:  # noqa: BLE001 — dedup is best-effort
            existing = None
        if existing is not None and getattr(existing, "storage_uri", None):
            stats.reused += 1
            await self.ledger.record(
                purpose="qa_image", cost_micros=0, status="reused",
                topic_slug=slug, prompt_hash=phash,
                fal_request_url=existing.storage_uri,
            )
            return existing.storage_uri, True

        # 2) Generate through the ledger guard (cap-checked + recorded). The
        # ledger charges the model+size-aware per-image cost (blackbox #3).
        seed = derive_seed(slug or topic, text)

        async def _gen() -> GenerateResult:
            # NO PHANTOM CHARGES (#3): if image generation is disabled / no FAL
            # key is wired, the client early-returns without a billable call —
            # we report billed=False so the ledger charges $0. A genuinely
            # billable call is one that REACHED FAL and completed; a failure
            # before FAL billed (raise) is also billed=False.
            if not self._fal_enabled_fn():
                return GenerateResult(url=None, billed=False)
            try:
                url = await self.client.generate(
                    prompt=prompt,
                    negative_prompt=built.get("negative_prompt") or None,
                    seed=seed,
                )
            except Exception:  # noqa: BLE001 — a failed call never reached billing
                logger.warning("qa_image.fal_generate_failed", exc_info=True)
                return GenerateResult(url=None, billed=False)
            # The call completed (FAL billed it) — even a post-call URL reject
            # (None) consumed quota, so it is billed.
            return GenerateResult(url=url, billed=True)

        spent_before_micros = await self.ledger.total_spent_micros()
        url = await self.ledger.guarded_generate(
            _gen, purpose="qa_image", topic_slug=slug, prompt_hash=phash,
            model=model, image_size=image_size,
        )
        delta_micros = max(0, await self.ledger.total_spent_micros() - spent_before_micros)
        stats.cost_micros += delta_micros

        if url is None:
            # No image. Either the cap blocked it (no spend), or no billable call
            # was made (no key / disabled). Classify via the spend delta.
            if delta_micros == 0:
                stats.blocked += 1
            return None, False

        # Persist a media_assets row so the NEXT build (or a crash re-run) dedups
        # this prompt and pays $0 — closing the cross-build reuse loop. We persist
        # even if the question later DISCARDS this image (all-or-none abort), so
        # the spend is reusable rather than wasted.
        await self._persist_media_asset(
            prompt=prompt, phash=phash, provider=provider, url=url
        )
        return url, False

    async def _persist_media_asset(
        self, *, prompt: str, phash: str, provider: str, url: str
    ) -> None:
        """Insert a ``media_assets`` row for a freshly-generated Q&A image.

        ``content_hash`` is unique; we derive it from the storage URI (the FAL
        CDN URL is itself content-addressed) so two distinct prompts that happen
        to yield the same image collapse to one row, and a re-run that produced
        the same URL is idempotent. The row is what ``find_media_asset_by_prompt_
        hash`` reads on the next build to skip the FAL call."""
        try:
            from app.models.db import MediaAsset  # noqa: PLC0415
            from app.services.precompute.dedup import content_hash  # noqa: PLC0415

            chash = content_hash({"qa_image_url": url})
            # Skip if a row with this content_hash already exists (idempotent).
            from sqlalchemy import select  # noqa: PLC0415

            existing = (
                await self.session.execute(
                    select(MediaAsset.id).where(
                        MediaAsset.content_hash == chash
                    ).limit(1)
                )
            ).first()
            if existing is not None:
                return
            self.session.add(
                MediaAsset(
                    content_hash=chash,
                    prompt_hash=phash,
                    storage_provider=provider or "fal",
                    storage_uri=url,
                    prompt_payload={"prompt": prompt, "purpose": "qa_image"},
                )
            )
            await self.session.flush()
        except Exception:  # noqa: BLE001 — persistence is best-effort
            logger.warning("qa_image.persist_media_asset_failed", exc_info=True)

    def _attach(self, target: dict, url: str, topic: str, text: str) -> None:
        from app.agent.tools.image_tools import qa_image_alt  # noqa: PLC0415

        target["image_url"] = url
        target["image_alt"] = qa_image_alt(topic=topic, text=text)
