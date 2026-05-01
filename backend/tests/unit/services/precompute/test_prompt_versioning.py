"""§21 Phase 8 — versioned prompts (`AC-PRECOMP-QUAL-1`)."""

from __future__ import annotations

import pytest

from app.services.precompute.prompts import Prompt, PromptRegistry


def test_prompt_version_and_hash_in_provenance():
    p = Prompt(name="synopsis_v1", semver="1.0.0", template="hello {topic}")
    prov = p.provenance()
    assert prov["name"] == "synopsis_v1"
    assert prov["semver"] == "1.0.0"
    assert len(prov["sha256"]) == 64
    # SHA is content-addressed: same template → same hash.
    p2 = Prompt(name="synopsis_v1", semver="1.0.0", template="hello {topic}")
    assert p2.sha256 == p.sha256


def test_rerun_with_new_version_creates_new_attempt():
    """A bumped semver MUST yield a different provenance — used by the
    builder to decide whether to re-attempt a topic on prompt-version
    rotation."""
    a = Prompt(name="evaluator_q", semver="1.0.0", template="judge")
    b = Prompt(name="evaluator_q", semver="1.1.0", template="judge")
    pa = a.provenance()
    pb = b.provenance()
    assert pa["semver"] != pb["semver"]
    assert pa != pb


def test_invalid_semver_rejected():
    reg = PromptRegistry()
    with pytest.raises(ValueError):
        reg.register(Prompt(name="x", semver="1.0", template="t"))


def test_registering_same_name_version_with_different_body_fails():
    reg = PromptRegistry()
    reg.register(Prompt(name="p", semver="1.0.0", template="A"))
    with pytest.raises(ValueError, match="bump semver"):
        reg.register(Prompt(name="p", semver="1.0.0", template="B"))


def test_registering_same_name_version_with_identical_body_is_idempotent():
    reg = PromptRegistry()
    reg.register(Prompt(name="p", semver="1.0.0", template="A"))
    reg.register(Prompt(name="p", semver="1.0.0", template="A"))
    assert len(reg.all()) == 1
