"""Flag-gated build hook for Q&A icon enrichment (DRAFT).

This is the ONLY entry point ``builder.py::run_build`` calls. It is the strict
no-op boundary required by the feature flag:

    * Flag OFF (default): ``maybe_bind_icons`` returns ``(artefact, 0)``
      IMMEDIATELY — before importing ``embedder`` / ``binder`` / ``index`` and
      before any DB read. No embedder is constructed, no model is loaded, the
      artefact is returned UNCHANGED (same object identity). ``run_build`` is
      therefore byte-for-byte today's behaviour.

    * Flag ON: the binder resolves an icon id for each Q&A string in the
      artefact and attaches it ADDITIVELY as optional fields. A string that
      routes below ``tau`` (or any failure) gets NO icon — never an error, never
      a behaviour change to the rest of the pipeline (fail-open / fail-quiet).

The hook is deliberately tolerant of the artefact shape (the orchestrator is
generator-agnostic: ``run_build`` only sees an opaque ``object``). It annotates
the common pack shapes — a mapping with a ``questions`` list, each question a
mapping with a stem (``question_text`` / ``text``) and an ``options`` list of
mappings each with a ``text`` — and leaves anything it doesn't recognise alone.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _icons_enabled(settings_obj: Any) -> bool:
    """Read the flag defensively; ANY problem -> treated as OFF (fail-closed)."""
    try:
        images = getattr(settings_obj, "images", None)
        return bool(getattr(images, "qa_icons_enabled", False))
    except Exception:  # pragma: no cover - defensive
        return False


def _generated_images_enabled(settings_obj: Any) -> bool:
    """Same-universe FAL generation sub-flag. Strictly downstream of
    ``qa_icons_enabled`` (the caller only reaches this on the flag-ON path), and
    fail-closed on any read problem so a misconfig can never spend by accident."""
    try:
        images = getattr(settings_obj, "images", None)
        return bool(getattr(images, "qa_generated_images_enabled", False))
    except Exception:  # pragma: no cover - defensive
        return False


async def maybe_bind_icons(
    db: Any,
    artefact: Any,
    *,
    settings_obj: Any | None = None,
) -> tuple[Any, int]:
    """Attach icon ids to ``artefact``'s Q&A iff the flag is ON.

    Returns ``(artefact, n_bound)``. ``n_bound`` is the number of strings that
    got an icon (0 when the flag is off — the strict no-op path).

    IMPORTANT: when the flag is off this returns BEFORE importing the embedder /
    binder / icon index, so nothing heavy is loaded.
    """
    if settings_obj is None:
        # Local import keeps this module importable without side effects.
        from app.core.config import settings as settings_obj  # noqa: PLC0415

    if not _icons_enabled(settings_obj):
        # ---- STRICT NO-OP: do not import embedder/binder; do not touch DB. ----
        return artefact, 0

    # ---- Flag ON path: everything heavy is imported lazily, here. ----
    # PRIORITY 2: same-universe FAL generation runs FIRST (when its sub-flag is
    # on) so a generated image is the preferred enrichment; the $0 generic-icon
    # binder then fills in the strings generation skipped (the fallback). Both
    # are additive and fail-open — neither can break a build.
    if _generated_images_enabled(settings_obj):
        await _maybe_generate_qa_images(db, artefact, settings_obj)

    try:
        from app.services.icons.binder import IconBinder
        from app.services.icons.embedder import raw_embed
        from app.services.icons.index import load_icon_index_from_db

        index = await load_icon_index_from_db(db)
        if not index:
            logger.warning("icons.bind.empty_index")
            return artefact, 0

        images = settings_obj.images
        binder = IconBinder(
            index=index,
            embed_fn=raw_embed,
            tau=float(images.tau),
            query_prefix=images.query_prefix,
        )
        n_bound = await _annotate_artefact(artefact, binder)
        logger.info("icons.bind.done", n_bound=n_bound, n_icons=len(index))
        return artefact, n_bound
    except Exception:  # noqa: BLE001 — fail-open: never break a build over icons
        logger.warning("icons.bind.failed", exc_info=True)
        return artefact, 0


async def _maybe_generate_qa_images(db: Any, artefact: Any, settings_obj: Any) -> None:
    """Generate + bind same-universe Q&A images (additive, fail-open).

    Imported lazily and constructed only here so the FAL client + generation
    pipeline are never touched unless BOTH flags are on. Any failure (incl. a
    missing FAL key) leaves the artefact untouched for the generic-icon binder.
    """
    try:
        from app.services.icons.fal_ledger import FalLedger
        from app.services.icons.qa_pipeline import QaImageGenerator
        from app.services.image_service import _client_singleton as fal_client

        images = settings_obj.images
        gate = _build_relevance_gate(images)
        gen = QaImageGenerator(
            session=db,
            ledger=FalLedger(db, config=images.fal_budget),
            client=fal_client,
            image_gen_cfg=settings_obj.image_gen,
            gate=gate,
            style_suffix=getattr(images, "qa_style_suffix", "") or None,
        )
        stats = await gen.enrich(artefact)
        logger.info("icons.qa_generate.done", **stats.as_dict())
    except Exception:  # noqa: BLE001 — fail-open: never break a build over images
        logger.warning("icons.qa_generate.failed", exc_info=True)


def _build_relevance_gate(images: Any):
    """Construct the per-string relevance gate (or None when disabled).

    Reuses the SAME 384-dim ``raw_embed`` and BGE ``query_prefix`` the icon
    binder uses, so the gate scores a Q&A string in exactly the embedding space
    the rest of the pipeline routes in. Any construction problem => no gate
    (attempt every string), which is strictly the safer-for-relevance failure
    only if the cap still protects spend; we therefore return None so the build
    continues but log it loudly."""
    try:
        gate_cfg = getattr(images, "relevance_gate", None)
        if gate_cfg is None or not getattr(gate_cfg, "enabled", True):
            return None
        from app.services.icons.embedder import raw_embed
        from app.services.icons.relevance_gate import RelevanceGate

        return RelevanceGate(
            embed_fn=raw_embed,
            query_prefix=getattr(images, "query_prefix", ""),
            margin=float(getattr(gate_cfg, "margin", 0.03)),
            concrete_floor=float(getattr(gate_cfg, "concrete_floor", 0.25)),
        )
    except Exception:  # noqa: BLE001 — never break a build constructing the gate
        logger.warning("icons.qa_generate.gate_build_failed", exc_info=True)
        return None


def _question_stem(q: Any) -> str | None:
    if not isinstance(q, dict):
        return None
    val = q.get("question_text") or q.get("text") or q.get("question")
    return val if isinstance(val, str) and val.strip() else None


async def _bind_text(target: dict, text: str | None, binder: Any) -> int:
    """Bind ``text`` and attach it to ``target`` additively. Returns 1 iff a new
    icon was attached, else 0 (no icon / blank text / pre-set / no match)."""
    if not (isinstance(text, str) and text.strip()):
        return 0
    binding = await binder.bind(text)
    if binding is not None and _attach(target, binding):
        return 1
    return 0


async def _annotate_artefact(artefact: Any, binder: Any) -> int:
    """Walk the artefact's questions/options and attach ``icon_id`` additively.

    Mutates ``artefact`` in place (additive optional fields only) and returns the
    number of strings bound. Tolerant: unrecognised shapes are skipped.
    """
    if not isinstance(artefact, dict):
        return 0
    questions = artefact.get("questions")
    if not isinstance(questions, list):
        return 0

    n_bound = 0
    for q in questions:
        if not isinstance(q, dict):
            continue
        n_bound += await _bind_text(q, _question_stem(q), binder)
        options = q.get("options")
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict):
                    n_bound += await _bind_text(opt, opt.get("text"), binder)
    return n_bound


def _attach(target: dict, binding: Any) -> bool:
    """Attach the resolved icon as additive, optional fields. Never overwrites
    an existing non-null value (idempotent / re-run safe). Returns True iff a new
    icon was attached."""
    if target.get("icon_id"):
        return False
    target["icon_id"] = binding.icon_id
    target["icon_palette_variant"] = binding.palette_variant
    target["icon_similarity"] = binding.similarity
    return True
