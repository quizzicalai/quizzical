"""Backwards-compatible shim for the signed starter-pack importer.

The canonical implementation now lives at
``app.services.precompute.pack_importer`` so that the FastAPI runtime
container image — which excludes ``backend/scripts/`` — can perform
``POST /api/v1/admin/precompute/import`` without a
``ModuleNotFoundError`` at request time.

This module re-exports the public surface (and the internal helpers
that downstream CLIs may construct around) so existing scripts like
``build_starter_packs.py`` and ``promote_user_quizzes.py`` keep working
unchanged. New code should import from
``app.services.precompute.pack_importer`` directly.
"""

from __future__ import annotations

from app.services.precompute.pack_importer import (  # noqa: F401
    UnsignedArchiveError,
    _get_or_create_baseline_question_set,
    _get_or_create_character_set,
    _get_or_create_synopsis,
    _import_one,
    _read_archive_from_disk,
    _upsert_characters_and_collect_ids,
    _upsert_questions_and_collect_ids,
    archive_sha256,
    has_any_published_pack,
    import_archive,
    sign_archive,
    verify_signature,
)

__all__ = [
    "UnsignedArchiveError",
    "archive_sha256",
    "has_any_published_pack",
    "import_archive",
    "sign_archive",
    "verify_signature",
]
