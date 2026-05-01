"""Unit tests for ``scripts/build_starter_packs.py``.

Covers determinism, signature correctness, schema shape, and end-to-end
import compatibility (build → sign → import → resolve_topic HIT).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_starter_packs import _canonical_json, build_archive, main
from scripts.import_packs import import_archive, sign_archive, verify_signature

SECRET = "build-test-secret-" + "x" * 32

_SOURCE_DOC = {
    "version": 1,
    "built_in_env": "starter",
    "topics": [
        {
            "slug": "alpha-quiz",
            "display_name": "Alpha Quiz",
            "aliases": ["Alpha", "Alpha Quiz!"],
            "synopsis": {
                "title": "Which Alpha are you?",
                "summary": "A canonical alpha synopsis.",
                "themes": ["a", "b", "c"],
                "tone": "warm",
            },
        },
    ],
}


def test_archive_is_deterministic():
    a = build_archive(_SOURCE_DOC)
    b = build_archive(_SOURCE_DOC)
    assert _canonical_json(a) == _canonical_json(b)


def test_archive_signature_verifies():
    archive = build_archive(_SOURCE_DOC)
    payload = _canonical_json(archive)
    sig = sign_archive(payload, secret=SECRET)
    assert verify_signature(payload, sig, secret=SECRET)


def test_archive_shape_matches_importer_contract():
    archive = build_archive(_SOURCE_DOC)
    assert "packs" in archive and len(archive["packs"]) == 1
    pack = archive["packs"][0]
    assert pack["topic"]["slug"] == "alpha-quiz"
    assert pack["aliases"] == ["Alpha", "Alpha Quiz!"]
    assert pack["synopsis"]["content_hash"].startswith("syn-")
    assert pack["character_set"]["composition_hash"].startswith("cs-")
    assert pack["baseline_question_set"]["composition_hash"].startswith("bqs-")
    assert pack["character_set"]["composition"] == {"character_ids": []}
    assert pack["baseline_question_set"]["composition"] == {"question_ids": []}
    assert pack["version"] == 1
    assert pack["built_in_env"] == "starter"


@pytest.mark.anyio
async def test_built_archive_imports_and_resolves(sqlite_db_session):
    from app.services.precompute.lookup import PrecomputeLookup

    archive = build_archive(_SOURCE_DOC)
    payload = _canonical_json(archive)
    sig = sign_archive(payload, secret=SECRET)

    out = await import_archive(
        sqlite_db_session, archive_payload=payload, signature=sig, secret=SECRET,
    )
    assert out["packs_inserted"] == 1

    lookup = PrecomputeLookup(db=sqlite_db_session, redis=None)
    # Alias-exact path.
    res = await lookup.resolve_topic("alpha")
    assert res is not None and res.via == "alias"
    # Slug-exact path.
    res = await lookup.resolve_topic("Alpha Quiz")
    assert res is not None


def test_cli_main_writes_archive_and_sig(tmp_path: Path, monkeypatch):
    src = tmp_path / "source.json"
    src.write_text(json.dumps(_SOURCE_DOC), encoding="utf-8")
    out = tmp_path / "archive.json"

    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", SECRET)
    rc = main(
        ["--source", str(src), "--out", str(out), "--secret-env", "PRECOMPUTE_HMAC_SECRET"]
    )
    assert rc == 0
    assert out.exists()
    sig_file = out.with_suffix(out.suffix + ".sig")
    assert sig_file.exists()
    sig = sig_file.read_text(encoding="utf-8").strip()
    assert verify_signature(out.read_bytes(), sig, secret=SECRET)


def test_cli_refuses_short_secret(tmp_path: Path, monkeypatch):
    src = tmp_path / "source.json"
    src.write_text(json.dumps(_SOURCE_DOC), encoding="utf-8")
    out = tmp_path / "archive.json"

    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", "tooshort")
    rc = main(
        ["--source", str(src), "--out", str(out), "--secret-env", "PRECOMPUTE_HMAC_SECRET"]
    )
    assert rc == 2
    assert not out.exists()


def test_real_starter_v1_source_builds_cleanly():
    """Sanity check: the committed source file is valid + deterministic."""
    repo_src = (
        Path(__file__).resolve().parents[3]
        / "configs"
        / "precompute"
        / "starter_packs"
        / "starter_v1.source.json"
    )
    assert repo_src.exists(), repo_src
    source = json.loads(repo_src.read_text(encoding="utf-8"))
    archive = build_archive(source)
    assert len(archive["packs"]) >= 5
    slugs = {p["topic"]["slug"] for p in archive["packs"]}
    assert {"disney-princess", "hogwarts-house"}.issubset(slugs)
    # Determinism re-check.
    assert _canonical_json(archive) == _canonical_json(build_archive(source))
