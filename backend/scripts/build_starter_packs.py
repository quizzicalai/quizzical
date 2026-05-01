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
          "character_set": {"composition_hash": sha256, "composition": {"character_ids": []}},
          "baseline_question_set": {"composition_hash": sha256, "composition": {"question_ids": []}},
          "version": 1,
          "built_in_env": "starter"
        }, ...
      ]
    }

Determinism (``AC-PRECOMP-MIGR-6``): hashes are computed from
``json.dumps(payload, sort_keys=True, separators=(",", ":"))`` so the same
source file always produces the same archive bytes (and therefore the same
signature for a given secret).

Usage::

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
import sys
from pathlib import Path
from typing import Any

from scripts.import_packs import sign_archive


def _canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON serialisation used for hashing + archive output."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _content_hash(prefix: str, payload: dict[str, Any]) -> str:
    """``<prefix>-<sha256-hex>`` — keeps the existing hash-prefix convention."""
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return f"{prefix}-{digest}"


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

        # Empty composition arrays — the live agent still produces characters
        # / baseline questions on /quiz/start. Phase 5 will populate these.
        cs_composition: dict[str, Any] = {"character_ids": []}
        bqs_composition: dict[str, Any] = {"question_ids": []}

        packs.append(
            {
                "topic": {"slug": slug, "display_name": display_name},
                "aliases": aliases,
                "synopsis": {
                    "content_hash": _content_hash("syn", synopsis_body),
                    "body": synopsis_body,
                },
                "character_set": {
                    "composition_hash": _content_hash(
                        "cs", {"slug": slug, "composition": cs_composition}
                    ),
                    "composition": cs_composition,
                },
                "baseline_question_set": {
                    "composition_hash": _content_hash(
                        "bqs", {"slug": slug, "composition": bqs_composition}
                    ),
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


__all__ = ["build_archive", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
