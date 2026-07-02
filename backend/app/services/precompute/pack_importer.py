"""Signed starter-pack archive importer (canonical location).

This module owns the runtime-critical logic that powers
``POST /api/v1/admin/precompute/import``. It lives under
``app.services.precompute`` (not ``scripts/``) so the production
container image — which intentionally excludes ``backend/scripts/``
to keep the runtime surface small — can perform seed/upgrade imports
without a ``ModuleNotFoundError`` at request time.

Acceptance criteria:
  - ``AC-PRECOMP-SEC-5``: archive must carry a detached HMAC-SHA256
    signature over its bytes; unsigned archives are refused.
  - ``AC-PRECOMP-MIGR-6``: idempotent on ``content_hash`` /
    ``composition_hash``; re-running an import is a no-op.
  - ``AC-PRECOMP-OBJ-2``: skipped when the destination DB already has
    at least one published ``topic_packs`` row (unless
    ``force_upgrade=True``).

The archive is a single JSON document (see
``configs/precompute/starter_packs/*.json``) of shape::

    {
      "packs": [
        {
          "topic": {"slug": "...", "display_name": "..."},
          "synopsis": {"content_hash": "...", "body": {...}},
          "character_set": {"composition_hash": "...", "composition": {...}},
          "baseline_question_set": {"composition_hash": "...", "composition": {...}},
          "version": 1,
          "built_in_env": "starter"
        }, ...
      ]
    }

A thin backwards-compat shim is preserved at
``backend/scripts/import_packs.py`` so existing CLI scripts
(``build_starter_packs``, ``promote_user_quizzes``) continue to import
from there unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    BaselineQuestionSet,
    Character,
    CharacterSet,
    Question,
    Synopsis,
    Topic,
    TopicAlias,
    TopicPack,
)
from app.services.precompute.canonicalize import canonical_key_for_name


class UnsignedArchiveError(Exception):
    """Raised when a starter-pack import is attempted with no valid signature."""


def archive_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sign_archive(payload: bytes, *, secret: str) -> str:
    """Returns hex HMAC-SHA256 signature."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, signature: str, *, secret: str) -> bool:
    expected = sign_archive(payload, secret=secret)
    return hmac.compare_digest(expected, signature)


async def has_any_published_pack(session: AsyncSession) -> bool:
    """`AC-PRECOMP-OBJ-2` precondition — only seed an empty DB."""
    n = (
        await session.execute(
            select(func.count(TopicPack.id)).where(TopicPack.status == "published")
        )
    ).scalar_one()
    return int(n or 0) > 0


async def import_archive(
    session: AsyncSession,
    *,
    archive_payload: bytes,
    signature: str,
    secret: str,
    force_upgrade: bool = False,
    redis=None,
) -> dict[str, int]:
    """Import a signed starter-pack archive.

    Returns a counters dict::

        {"packs_inserted": N, "packs_skipped": M, "skipped_db_not_empty": 0|1}

    Raises `UnsignedArchiveError` on missing/invalid signature.

    ``force_upgrade=True`` bypasses the ``AC-PRECOMP-OBJ-2`` global
    "skip if DB already has any published pack" gate. Per-pack idempotency
    on ``(topic_id, version)`` still prevents duplicate inserts, so this is
    safe for re-seeding production with a higher pack version.

    P11 (2026-07-02) — when ``redis`` is provided, the serve-path caches for
    every touched topic/pack are invalidated after commit. This matters even
    for "skipped" packs: a re-import of an unchanged composition still
    refreshes ``Character.image_url`` in place (curated art), so the cached
    ``HydratedPack`` for the existing pack_id would otherwise serve stale art
    for up to its TTL. Invalidation is fail-open (never raises).
    """
    if not signature or not verify_signature(archive_payload, signature, secret=secret):
        raise UnsignedArchiveError(
            "starter-pack archive signature missing or invalid — refusing import"
        )

    if not force_upgrade and await has_any_published_pack(session):
        return {"packs_inserted": 0, "packs_skipped": 0, "skipped_db_not_empty": 1}

    archive_hash = archive_sha256(archive_payload)
    doc = json.loads(archive_payload.decode("utf-8"))
    inserted = skipped = 0
    touched: list[tuple[uuid.UUID, uuid.UUID]] = []  # (topic_id, pack_id)
    for entry in doc.get("packs", []):
        added, topic_id, pack_id = await _import_one(
            session, entry, imported_from=archive_hash
        )
        if added:
            inserted += 1
        else:
            skipped += 1
        if topic_id is not None and pack_id is not None:
            touched.append((topic_id, pack_id))
    await session.commit()

    if redis is not None and touched:
        from app.services.precompute import cache as pack_cache

        for topic_id, pack_id in touched:
            await pack_cache.invalidate_pack(redis, topic_id)
            await pack_cache.invalidate_hydrated_pack(redis, pack_id)

    return {
        "packs_inserted": inserted,
        "packs_skipped": skipped,
        "skipped_db_not_empty": 0,
    }


async def _get_or_create_synopsis(
    session: AsyncSession, *, topic_id: uuid.UUID, entry: dict
) -> Synopsis:
    syn_hash = entry["synopsis"]["content_hash"]
    syn = (
        await session.execute(select(Synopsis).where(Synopsis.content_hash == syn_hash))
    ).scalar_one_or_none()
    if syn is None:
        syn = Synopsis(
            id=uuid.uuid4(),
            topic_id=topic_id,
            content_hash=syn_hash,
            body=entry["synopsis"]["body"],
        )
        session.add(syn)
    return syn


async def _get_or_create_character_set(
    session: AsyncSession, *, entry: dict
) -> CharacterSet:
    cs_hash = entry["character_set"]["composition_hash"]
    composition_in = dict(entry["character_set"]["composition"] or {})
    inline_chars = list(entry.get("characters") or [])
    have_inline = bool(inline_chars and "character_keys" in composition_in)

    # Always walk the inline character entries (when the archive carries
    # them) so ``_upsert_characters_and_collect_ids`` refreshes
    # ``Character.image_url`` even on re-import of an unchanged
    # composition. The composition_hash is computed from character
    # names/keys only — regenerated image URLs do NOT change it, so
    # without this unconditional refresh the curated art from the signed
    # archive would never reach prod on the common re-seed path.
    char_ids = (
        await _upsert_characters_and_collect_ids(session, inline_chars)
        if have_inline
        else []
    )

    cs = (
        await session.execute(
            select(CharacterSet).where(CharacterSet.composition_hash == cs_hash)
        )
    ).scalar_one_or_none()
    if cs is not None:
        return cs

    composition_out: dict = (
        {"character_ids": [str(c) for c in char_ids]} if have_inline else composition_in
    )
    cs = CharacterSet(
        id=uuid.uuid4(),
        composition_hash=cs_hash,
        composition=composition_out,
    )
    session.add(cs)
    return cs


async def _get_or_create_baseline_question_set(
    session: AsyncSession, *, entry: dict
) -> BaselineQuestionSet:
    bqs_hash = entry["baseline_question_set"]["composition_hash"]
    bqs = (
        await session.execute(
            select(BaselineQuestionSet).where(
                BaselineQuestionSet.composition_hash == bqs_hash
            )
        )
    ).scalar_one_or_none()
    if bqs is not None:
        return bqs
    bqs_composition_in = dict(entry["baseline_question_set"]["composition"] or {})
    inline_questions = list(entry.get("questions") or [])
    if inline_questions and "question_keys" in bqs_composition_in:
        q_ids = await _upsert_questions_and_collect_ids(session, inline_questions)
        bqs_composition_out: dict = {"question_ids": [str(q) for q in q_ids]}
    else:
        bqs_composition_out = bqs_composition_in
    bqs = BaselineQuestionSet(
        id=uuid.uuid4(),
        composition_hash=bqs_hash,
        composition=bqs_composition_out,
    )
    session.add(bqs)
    return bqs


async def _import_one(
    session: AsyncSession, entry: dict, *, imported_from: str
) -> tuple[bool, uuid.UUID | None, uuid.UUID | None]:
    """Insert a single pack idempotently.

    Returns ``(created, topic_id, pack_id)`` — ``created`` is True when a new
    pack row was inserted; ``pack_id`` is the new row's id, or the EXISTING
    pack's id when the (topic_id, version) pair was already present (the
    caller uses it to invalidate serve-path caches, since a "skipped" import
    can still refresh character art in place)."""
    topic_slug = entry["topic"]["slug"]
    topic = (
        await session.execute(select(Topic).where(Topic.slug == topic_slug))
    ).scalar_one_or_none()
    if topic is None:
        topic = Topic(
            id=uuid.uuid4(),
            slug=topic_slug,
            display_name=entry["topic"]["display_name"],
        )
        session.add(topic)
        await session.flush()

    syn = await _get_or_create_synopsis(session, topic_id=topic.id, entry=entry)
    cs = await _get_or_create_character_set(session, entry=entry)
    bqs = await _get_or_create_baseline_question_set(session, entry=entry)

    await session.flush()

    # Idempotency on (topic_id, version) — re-running with the same
    # archive must not duplicate.
    version = int(entry.get("version", 1))
    existing = (
        await session.execute(
            select(TopicPack).where(
                TopicPack.topic_id == topic.id, TopicPack.version == version
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False, topic.id, existing.id

    pack = TopicPack(
        id=uuid.uuid4(),
        topic_id=topic.id,
        version=version,
        status="published",
        synopsis_id=syn.id,
        character_set_id=cs.id,
        baseline_question_set_id=bqs.id,
        model_provenance={"imported_from": imported_from},
        built_in_env=entry.get("built_in_env", "starter"),
    )
    session.add(pack)
    await session.flush()

    # Wire the topic's read-path pointer so `PrecomputeLookup` can return a
    # HIT immediately (it requires `topics.current_pack_id` AND the pack to
    # be `status='published'`). Without this, the freshly imported pack is
    # invisible to /quiz/start.
    topic.current_pack_id = pack.id

    # Optional alias rows. Each alias becomes a `topic_aliases` entry keyed
    # on the canonicalised key (`canonical_key_for_name`). Idempotent: skip
    # duplicates so re-running an import is safe.
    for alias_text in entry.get("aliases", []) or []:
        if not isinstance(alias_text, str) or not alias_text.strip():
            continue
        normalized = canonical_key_for_name(alias_text)
        if not normalized:
            continue
        exists = (
            await session.execute(
                select(TopicAlias).where(
                    TopicAlias.alias_normalized == normalized,
                    TopicAlias.topic_id == topic.id,
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            TopicAlias(
                alias_normalized=normalized,
                topic_id=topic.id,
                display_alias=alias_text.strip(),
            )
        )

    await session.flush()
    return True, topic.id, pack.id


def _read_archive_from_disk(path: Path) -> bytes:
    return Path(path).read_bytes()


async def _upsert_characters_and_collect_ids(
    session: AsyncSession, inline_chars: list[dict]
) -> list[uuid.UUID]:
    """Idempotently upsert Character rows by ``name`` and return their IDs
    in input order. Used by the v2+ pack import to translate
    ``character_keys`` → ``character_ids`` for the persisted CharacterSet
    composition. Skips entries with empty name / short_description /
    profile_text (DB CHECK constraints would reject them anyway).

    From v3 onward, each entry may also carry ``image_url``. The signed
    archive is the curated source-of-truth for character art — when the
    archive supplies a non-empty ``image_url`` that differs from the
    persisted value we overwrite (this is how regenerated branded character
    art reaches prod via the seed workflow). Re-seeding with the same URL
    is a no-op. An archive entry without ``image_url`` never clears a value
    already stored in the DB.
    """
    ids: list[uuid.UUID] = []
    for ch in inline_chars:
        name = (ch.get("name") or "").strip()
        short_desc = (ch.get("short_description") or "").strip()
        profile_text = (ch.get("profile_text") or "").strip()
        image_url = (ch.get("image_url") or "").strip() or None
        if not (name and short_desc and profile_text):
            continue
        existing = (
            await session.execute(select(Character).where(Character.name == name))
        ).scalar_one_or_none()
        if existing is None:
            row = Character(
                id=uuid.uuid4(),
                name=name,
                short_description=short_desc,
                profile_text=profile_text,
                canonical_key=canonical_key_for_name(name),
                image_url=image_url,
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
        else:
            # Archive is the curated source-of-truth: overwrite whenever the
            # archive provides a non-empty image_url that differs from what
            # we have. Never clear an existing value with a None from the
            # archive (preserves URLs FAL has filled in at request time for
            # legacy packs that shipped without character art).
            if image_url and image_url != getattr(existing, "image_url", None):
                existing.image_url = image_url
            ids.append(existing.id)
    return ids


async def _upsert_questions_and_collect_ids(
    session: AsyncSession, inline_questions: list[dict]
) -> list[uuid.UUID]:
    """Idempotently upsert ``Question`` rows by ``text_hash`` and return
    their IDs in input order. Used by v3+ pack imports to translate
    ``question_keys`` → ``question_ids`` for the persisted
    ``BaselineQuestionSet.composition``. Skips entries with empty text
    or no options.
    """
    ids: list[uuid.UUID] = []
    for q in inline_questions:
        text = (q.get("text") or "").strip()
        options = list(q.get("options") or [])
        text_hash = (q.get("text_hash") or "").strip()
        kind = (q.get("kind") or "baseline").strip() or "baseline"
        if not (text and options and text_hash):
            continue
        existing = (
            await session.execute(
                select(Question).where(Question.text_hash == text_hash)
            )
        ).scalar_one_or_none()
        if existing is None:
            row = Question(
                id=uuid.uuid4(),
                text_hash=text_hash,
                text=text,
                options={"items": options},
                kind=kind,
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
        else:
            ids.append(existing.id)
    return ids


__all__ = [
    "UnsignedArchiveError",
    "archive_sha256",
    "has_any_published_pack",
    "import_archive",
    "sign_archive",
    "verify_signature",
]
