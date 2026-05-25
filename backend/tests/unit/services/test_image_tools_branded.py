"""Tests for the branded-character prompt builders and helpers."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


def test_build_branded_attempt_prompt_includes_name_and_source():
    from app.agent.tools import image_tools

    spec = image_tools.build_branded_attempt_prompt(
        name="Aragorn",
        source="The Lord of the Rings",
        style_suffix="cinematic painterly portrait",
        negative_prompt="low quality, watermark",
    )
    assert "prompt" in spec and "negative_prompt" in spec
    assert "Aragorn from The Lord of the Rings" in spec["prompt"]
    assert image_tools.STYLE_ANCHOR in spec["prompt"]
    assert "cinematic painterly portrait" in spec["prompt"]
    assert spec["negative_prompt"] == "low quality, watermark"


def test_build_branded_attempt_prompt_handles_missing_source():
    from app.agent.tools import image_tools

    spec = image_tools.build_branded_attempt_prompt(
        name="Maomao",
        source="",
        style_suffix="x",
        negative_prompt="y",
    )
    assert spec["prompt"].startswith("Maomao, illustrated character portrait")
    assert image_tools.STYLE_ANCHOR in spec["prompt"]


def test_build_descriptive_attempt_prompt_truncates_and_anchors():
    from app.agent.tools import image_tools

    long_desc = "A " + ("very " * 200) + "tall figure."
    spec = image_tools.build_descriptive_attempt_prompt(
        description=long_desc,
        style_suffix="suffix",
        negative_prompt="neg",
    )
    assert image_tools.STYLE_ANCHOR in spec["prompt"]
    assert len(spec["prompt"]) <= image_tools._MAX_PROMPT_CHARS
    assert spec["negative_prompt"] == "neg"
