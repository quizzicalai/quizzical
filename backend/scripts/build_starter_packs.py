"""§21 Phase 9 — `scripts/build_starter_packs.py`.

Reads the hand-authored source file at
``backend/configs/precompute/starter_packs/starter_v1.source.json`` and
emits a deterministic, signed archive pair:

  - ``starter_v1.json``  — the canonical archive bytes consumed by
    :func:`scripts.import_packs.import_archive`.
  - ``starter_v1.json.sig`` — hex HMAC-SHA256 detached signature.

The archive shape matches the contract documented in ``import_packs``::

    {
      "packs": [
        {
          "topic": {"slug": ..., "display_name": ...},
          "aliases": [...],
          "synopsis": {"content_hash": sha256, "body": {...}},
          "characters": [{"name", "short_description", "profile_text", "image_url"?}, ...],
          "character_set": {"composition_hash": sha256, "composition": {"character_keys": [...]}},
          "questions": [{"text_hash": sha256, "text": ..., "options": [...]}, ...],
          "baseline_question_set": {"composition_hash": sha256, "composition": {"question_keys": [...]}},
          "version": 3,
          "built_in_env": "starter"
        }, ...
      ]
    }

    From v2 onward, each pack carries inline character profile text. From
    v3 onward, packs also carry inline baseline questions. The importer
    upserts Character / Question rows by canonical name / text_hash and
    rewrites compositions to id-references on the way in. The archive
    itself stays content-addressed (no DB UUIDs) so it remains
    byte-identical across environments.
    python -m scripts.build_starter_packs \
        --source backend/configs/precompute/starter_packs/starter_v1.source.json \
        --out    backend/configs/precompute/starter_packs/starter_v1.json \
        --secret-env PRECOMPUTE_HMAC_SECRET
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Canonicalise character names the same way the importer does, so the
# composition_hash on the source side matches whatever the DB will end up
# storing as ``characters.canonical_key``. Lazy import keeps this script
# usable from a thin CI container that hasn't booted the full app yet.
from app.services.precompute.canonicalize import canonical_key_for_name
from scripts.import_packs import sign_archive


# Control characters that PostgreSQL TEXT columns cannot store and which
# corrupt prompts when round-tripped through LLMs. We strip every C0
# control byte except TAB (0x09), LF (0x0A), and CR (0x0D); we also strip
# the UTF-8 BOM (U+FEFF) which sometimes appears mid-string when an LLM
# concatenates fragments. Stripping happens recursively over every str
# in the archive document just before serialization, so a single bad
# byte from any upstream source (LLM hallucination, mojibake decode,
# manual edit) cannot make it into the signed bytes.
_ILLEGAL_TEXT_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufeff]")


def sanitize_text(value: str) -> str:
    """Strip NUL/C0 control bytes (except \\t \\n \\r) and the BOM from ``value``.

    PostgreSQL refuses to store NUL (0x00) bytes in TEXT columns; an LLM
    occasionally emits one as a replacement for a non-ASCII character
    (e.g. ``macram\u0000`` instead of ``macram\u00e9``). We strip them
    here so the import endpoint never sees them.
    """
    if not value:
        return value
    return _ILLEGAL_TEXT_RE.sub("", value)


def _sanitize_archive(node: Any) -> Any:
    """Recursively walk the archive document and strip illegal bytes
    from every string value. Lists and dicts are reconstructed in place."""
    if isinstance(node, str):
        return sanitize_text(node)
    if isinstance(node, dict):
        return {k: _sanitize_archive(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_sanitize_archive(v) for v in node]
    return node


def _canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON serialisation used for hashing + archive output."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _content_hash(prefix: str, payload: dict[str, Any]) -> str:
    """``<prefix>-<sha256-hex>`` — keeps the existing hash-prefix convention."""
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return f"{prefix}-{digest}"


def _build_characters(
    raw_characters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise inline character entries (v2+).

    Returns ``(characters, character_keys)`` where ``character_keys`` is the
    canonical-name list used to derive the deterministic composition hash.
    """
    characters: list[dict[str, Any]] = []
    character_keys: list[str] = []
    for ch in raw_characters:
        name = (ch.get("name") or "").strip()
        if not name:
            continue
        key = canonical_key_for_name(name)
        if not key:
            continue
        char_entry: dict[str, Any] = {
            "name": name,
            "short_description": (ch.get("short_description") or "").strip(),
            "profile_text": (ch.get("profile_text") or "").strip(),
        }
        if ch.get("image_url"):
            char_entry["image_url"] = str(ch["image_url"]).strip()
        characters.append(char_entry)
        character_keys.append(key)
    return characters, character_keys


def _normalise_question_options(opts_raw: list[Any]) -> list[dict[str, Any]]:
    opts: list[dict[str, Any]] = []
    for opt in opts_raw:
        if isinstance(opt, dict):
            text_v = (opt.get("text") or "").strip()
            if not text_v:
                continue
            cleaned: dict[str, Any] = {"text": text_v}
            if opt.get("image_url"):
                cleaned["image_url"] = str(opt["image_url"]).strip()
            opts.append(cleaned)
        elif isinstance(opt, str) and opt.strip():
            opts.append({"text": opt.strip()})
    return opts


def _build_questions(
    raw_questions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise inline baseline questions (v3+).

    Returns ``(questions_archive, question_keys)`` where ``question_keys``
    is the list of ``text_hash`` values used as composition references.
    """
    questions_archive: list[dict[str, Any]] = []
    question_keys: list[str] = []
    for q in raw_questions:
        q_text = (q.get("question_text") or "").strip()
        if not q_text:
            continue
        opts = _normalise_question_options(list(q.get("options") or []))
        if not opts:
            continue
        q_hash = _content_hash("q", {"text": q_text, "options": opts})
        questions_archive.append(
            {
                "text_hash": q_hash,
                "text": q_text,
                "options": opts,
                "kind": "baseline",
            }
        )
        question_keys.append(q_hash)
    return questions_archive, question_keys


def build_archive(source: dict[str, Any]) -> dict[str, Any]:
    """Convert a hand-authored source doc into the archive document.

    The source format is small and human-friendly; we expand it here into the
    full schema the importer expects. Synopses get a real ``content_hash`` so
    re-running the build with unchanged content yields byte-identical output.
    """
    version = int(source.get("version", 1))
    built_in_env = source.get("built_in_env", "starter")

    packs: list[dict[str, Any]] = []
    for entry in source.get("topics", []):
        slug = entry["slug"]
        display_name = entry["display_name"]
        aliases = list(entry.get("aliases", []) or [])
        synopsis_body = entry["synopsis"]

        characters, character_keys = _build_characters(
            list(entry.get("characters") or [])
        )

        # Composition is keyed by canonical character keys (deterministic,
        # environment-independent). The importer translates these into
        # ``character_ids`` after upserting Character rows. Legacy packs
        # without inline characters still emit ``{"character_ids": []}`` so
        # the on-disk shape and composition_hash stay byte-identical with
        # v1 archives.
        if characters:
            cs_composition: dict[str, Any] = {"character_keys": character_keys}
            cs_hash_payload: dict[str, Any] = {
                "slug": slug,
                "composition": cs_composition,
                "characters": characters,
            }
        else:
            cs_composition = {"character_ids": []}
            cs_hash_payload = {"slug": slug, "composition": cs_composition}

        questions_archive, question_keys = _build_questions(
            list(entry.get("baseline_questions") or [])
        )

        if question_keys:
            bqs_composition: dict[str, Any] = {"question_keys": question_keys}
            bqs_hash_payload: dict[str, Any] = {
                "slug": slug,
                "composition": bqs_composition,
                "questions": questions_archive,
            }
        else:
            bqs_composition = {"question_ids": []}
            bqs_hash_payload = {"slug": slug, "composition": bqs_composition}

        packs.append(
            {
                "topic": {"slug": slug, "display_name": display_name},
                "aliases": aliases,
                "synopsis": {
                    "content_hash": _content_hash("syn", synopsis_body),
                    "body": synopsis_body,
                },
                "characters": characters,
                "character_set": {
                    "composition_hash": _content_hash("cs", cs_hash_payload),
                    "composition": cs_composition,
                },
                "questions": questions_archive,
                "baseline_question_set": {
                    "composition_hash": _content_hash("bqs", bqs_hash_payload),
                    "composition": bqs_composition,
                },
                "version": version,
                "built_in_env": built_in_env,
            }
        )

    return {"packs": packs}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--secret-env",
        default="PRECOMPUTE_HMAC_SECRET",
        help="Env var name that holds the HMAC secret (>=32 bytes).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    secret = (os.getenv(args.secret_env) or "").strip()
    if len(secret) < 32:
        print(
            f"!! Env var {args.secret_env} missing or shorter than 32 bytes "
            "(refusing to sign starter pack archive)",
            file=sys.stderr,
        )
        return 2

    source_doc = json.loads(args.source.read_text(encoding="utf-8"))
    archive_doc = build_archive(source_doc)
    # Sanitize before hashing/signing so the signed bytes are guaranteed
    # to round-trip cleanly through PostgreSQL TEXT columns.
    archive_doc = _sanitize_archive(archive_doc)
    archive_bytes = _canonical_json(archive_doc)
    signature = sign_archive(archive_bytes, secret=secret)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(archive_bytes)
    sig_path = args.out.with_suffix(args.out.suffix + ".sig")
    sig_path.write_text(signature, encoding="utf-8")

    print(f"== wrote archive: {args.out} ({len(archive_bytes)} bytes)")
    print(f"== wrote sig:     {sig_path}")
    print(f"== sha256:        {hashlib.sha256(archive_bytes).hexdigest()}")
    print(f"== packs:         {len(archive_doc['packs'])}")
    return 0


__all__ = ["build_archive", "main", "sanitize_text"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
