"""/config YAML loader: size-cap + safe_load only."""
from __future__ import annotations

import yaml

from app.api.endpoints import config as cfg_mod


def test_yaml_loader_uses_safe_load_not_unsafe_load() -> None:
    # Ruby-style YAML "!!python/object" payloads must not be deserialized.
    # safe_load raises ConstructorError; the loader catches it and returns {}.
    payload = "!!python/object/apply:os.system ['echo pwn']\n"
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(payload)
        path = f.name
    try:
        out = cfg_mod._load_yaml_config(path)
        assert out == {}
    finally:
        import os
        os.unlink(path)


def test_yaml_loader_rejects_oversized_file(monkeypatch, tmp_path) -> None:
    # Cap at 100 bytes so we can trip the guard with a tiny test file.
    monkeypatch.setattr(cfg_mod, "_CONFIG_MAX_BYTES", 100, raising=False)
    big = tmp_path / "huge.yaml"
    big.write_text("a: " + ("x" * 500))
    out = cfg_mod._load_yaml_config(big)
    assert out == {}, "Oversized YAML must be refused, not parsed"


def test_yaml_loader_handles_normal_file(tmp_path) -> None:
    p = tmp_path / "ok.yaml"
    p.write_text("quizzical:\n  frontend:\n    features:\n      turnstile: true\n")
    out = cfg_mod._load_yaml_config(p)
    assert isinstance(out, dict)
    assert out["quizzical"]["frontend"]["features"]["turnstile"] is True


def test_yaml_loader_returns_empty_for_missing_path(tmp_path) -> None:
    out = cfg_mod._load_yaml_config(tmp_path / "nope.yaml")
    assert out == {}


def test_yaml_loader_returns_empty_for_non_dict_root(tmp_path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- one\n- two\n")
    # Sanity: yaml does parse this as a list.
    assert isinstance(yaml.safe_load(p.read_text()), list)
    # But the loader normalizes to {} for safety since the rest of the
    # code expects a dict shape.
    out = cfg_mod._load_yaml_config(p)
    assert out == {}
