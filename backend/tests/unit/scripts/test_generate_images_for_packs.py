from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts._precompute_spend import SpendLedger
from scripts.generate_images_for_packs import ImageEvalResult, generate_images_for_packs


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _sample_source() -> dict:
    return {
        "version": 3,
        "built_in_env": "starter",
        "topics": [
            {
                "slug": "demo-topic",
                "display_name": "Demo Topic",
                "characters": [
                    {
                        "name": "Hero",
                        "short_description": "A bold and thoughtful protagonist.",
                        "profile_text": "Leads with courage and strategy.",
                        "image_url": "https://v3b.fal.media/files/demo.jpg",
                    }
                ],
                "synopsis": {"title": "T", "summary": "S"},
                "baseline_questions": [],
            }
        ],
    }


def _sample_report() -> dict:
    return {
        "topics": [
            {
                "slug": "demo-topic",
                "ready": True,
                "judge_passed": True,
            }
        ]
    }


def test_evaluate_existing_keeps_passing_image(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    out_path = tmp_path / "out.json"
    _write_json(source_path, _sample_source())
    _write_json(report_path, _sample_report())

    async def _judge(**_: object) -> ImageEvalResult:
        return ImageEvalResult(
            score=95,
            passed=True,
            blocking_reasons=[],
            notes=["ok"],
        )

    monkeypatch.setattr("scripts.generate_images_for_packs.llm_image_judge", _judge)

    ledger = SpendLedger(cap_cents=10_000)
    stats = asyncio.run(
        generate_images_for_packs(
            source_path=source_path,
            report_path=report_path,
            out_path=out_path,
            spend_ledger=ledger,
            evaluate_existing=True,
        )
    )

    written = json.loads(out_path.read_text(encoding="utf-8"))
    image_url = written["topics"][0]["characters"][0].get("image_url")

    assert image_url == "https://v3b.fal.media/files/demo.jpg"
    assert stats["total_images_evaluated"] == 1
    assert stats["total_images_passed"] == 1
    assert stats["total_images_failed"] == 0


def test_evaluate_existing_clears_failing_image(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    out_path = tmp_path / "out.json"
    _write_json(source_path, _sample_source())
    _write_json(report_path, _sample_report())

    async def _judge(**_: object) -> ImageEvalResult:
        return ImageEvalResult(
            score=42,
            passed=False,
            blocking_reasons=["style_mismatch"],
            notes=["bad"],
        )

    monkeypatch.setattr("scripts.generate_images_for_packs.llm_image_judge", _judge)

    ledger = SpendLedger(cap_cents=10_000)
    stats = asyncio.run(
        generate_images_for_packs(
            source_path=source_path,
            report_path=report_path,
            out_path=out_path,
            spend_ledger=ledger,
            evaluate_existing=True,
        )
    )

    written = json.loads(out_path.read_text(encoding="utf-8"))
    image_url = written["topics"][0]["characters"][0].get("image_url")

    assert image_url is None
    assert stats["total_images_evaluated"] == 1
    assert stats["total_images_passed"] == 0
    assert stats["total_images_failed"] == 1
