"""§21 Phase 9 — `scripts/import_packs.py` (signed starter pack import).

Acceptance:
  - `AC-PRECOMP-SEC-5`: archive must carry a detached HMAC-SHA256
    signature over its bytes; unsigned archives are refused.
  - `AC-PRECOMP-MIGR-6`: idempotent on `content_hash` / `composition_hash`;
    re-running an import is a no-op.
  - `AC-PRECOMP-OBJ-2`: skipped when the destination DB already has at
    least one published `topic_packs` row.

The archive is a single JSON document (see
`configs/precompute/starter_packs/*.json`) of shape::

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
) -> dict[str, int]:
    """Import a signed starter-pack archive.

    Returns a counters dict::

        {"packs_inserted": N, "packs_skipped": M, "skipped_db_not_empty": 0|1}

    Raises `UnsignedArchiveError` on missing/invalid signature.

    ``force_upgrade=True`` bypasses the ``AC-PRECOMP-OBJ-2`` global
    "skip if DB already has any published pack" gate. Per-pack idempotency
    on ``(topic_id, version)`` still prevents duplicate inserts, so this is
    safe for re-seeding production with a higher pack version.
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
    for entry in doc.get("packs", []):
        added = await _import_one(session, entry, imported_from=archive_hash)
        if added:
            inserted += 1
        else:
            skipped += 1
    await session.commit()
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
    cs = (
        await session.execute(
            select(CharacterSet).where(CharacterSet.composition_hash == cs_hash)
        )
    ).scalar_one_or_none()
    if cs is not None:
        return cs
    composition_in = dict(entry["character_set"]["composition"] or {})
    inline_chars = list(entry.get("characters") or [])
    if inline_chars and "character_keys" in composition_in:
        char_ids = await _upsert_characters_and_collect_ids(session, inline_chars)
        composition_out: dict = {"character_ids": [str(c) for c in char_ids]}
    else:
        composition_out = composition_in
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
) -> bool:
    """Insert a single pack idempotently. Returns True if a new pack row
    was created, False if nothing changed."""
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
        return False

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
    return True


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

    From v3 onward, each entry may also carry ``image_url`` — if the
    Character row exists with a NULL ``image_url`` we backfill it from
    the archive (preserving any curated value already on disk).
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
            # Backfill image_url only when not already set; never overwrite
            # a curated URL.
            if image_url and not getattr(existing, "image_url", None):
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
