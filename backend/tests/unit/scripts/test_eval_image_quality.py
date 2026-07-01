"""Unit tests for scripts/eval_image_quality.py — NO real network/LLM calls.

A FAKE vision client returns canned scores so we can assert:
  * the verdict logic (pass / fail / blocking),
  * the --max-spend cap (fail-safe + skipped-budget for the remainder),
  * the dead-URL -> unavailable path (not a silent pass),
  * the pass-rate gate + exit code,
  * the read-only default (no DB/image writes without --write-scores),
  * the tolerant JSON judge parser.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts._precompute_spend import SpendLedger
from scripts.eval_image_quality import (
    ImageItem,
    ImageVerdict,
    VisionScore,
    aggregate,
    evaluate_images,
    fetch_image_data_url,
    main,
    parse_vision_score,
    verdict_from_score,
)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeVisionClient:
    """Returns a queued score per call; records calls; can raise on demand."""

    def __init__(self, scores: list[VisionScore], raise_on: set[int] | None = None):
        self._scores = list(scores)
        self._raise_on = raise_on or set()
        self.calls = 0
        self.seen_data_urls: list[str] = []

    async def score(self, *, image_data_url: str, **_: object) -> VisionScore:
        idx = self.calls
        self.calls += 1
        self.seen_data_urls.append(image_data_url)
        if idx in self._raise_on:
            raise RuntimeError("simulated vision error")
        return self._scores[idx] if idx < len(self._scores) else self._scores[-1]


class FakeResponse:
    def __init__(self, status_code: int, content: bytes, content_type: str = "image/png"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


class FakeHttpClient:
    """Maps URL -> FakeResponse; missing/None URLs raise (-> unavailable)."""

    def __init__(self, mapping: dict[str, FakeResponse]):
        self._mapping = mapping

    async def get(self, url: str, timeout: float | int | None = None) -> FakeResponse:
        if url not in self._mapping:
            raise RuntimeError("connection refused")
        return self._mapping[url]


def _good() -> VisionScore:
    return VisionScore(fidelity=9, relevance=8, style_ok=True, blocking_reasons=[])


def _bad_blocker() -> VisionScore:
    return VisionScore(
        fidelity=9, relevance=9, style_ok=True, blocking_reasons=["deformed_face"]
    )


def _low_fidelity() -> VisionScore:
    return VisionScore(fidelity=4, relevance=9, style_ok=True, blocking_reasons=[])


# ---------------------------------------------------------------------------
# verdict logic
# ---------------------------------------------------------------------------


def test_verdict_pass_requires_all_conditions():
    assert verdict_from_score(_good()) == "pass"


def test_verdict_blocking_reason_fails_even_with_high_scores():
    assert verdict_from_score(_bad_blocker()) == "fail"


def test_verdict_low_fidelity_fails():
    assert verdict_from_score(_low_fidelity()) == "fail"
    assert (
        verdict_from_score(
            VisionScore(fidelity=9, relevance=4, style_ok=True, blocking_reasons=[])
        )
        == "fail"
    )
    assert (
        verdict_from_score(
            VisionScore(fidelity=9, relevance=9, style_ok=False, blocking_reasons=[])
        )
        == "fail"
    )


# ---------------------------------------------------------------------------
# core loop: pass/fail/blocking via fake client + fake http
# ---------------------------------------------------------------------------


def test_evaluate_mixed_pass_fail_and_blocking():
    items = [
        ImageItem(subject="Hero", topic="Demo", image_url="http://x/a.png"),
        ImageItem(subject="Villain", topic="Demo", image_url="http://x/b.png"),
        ImageItem(subject="Sidekick", topic="Demo", image_url="http://x/c.png"),
    ]
    http = FakeHttpClient(
        {
            "http://x/a.png": FakeResponse(200, _TINY_PNG),
            "http://x/b.png": FakeResponse(200, _TINY_PNG),
            "http://x/c.png": FakeResponse(200, _TINY_PNG),
        }
    )
    client = FakeVisionClient([_good(), _bad_blocker(), _low_fidelity()])
    ledger = SpendLedger(cap_cents=10_000)

    verdicts = asyncio.run(
        evaluate_images(
            items,
            vision_client=client,
            judge_model="fake",
            spend_ledger=ledger,
            http_client=http,
        )
    )
    assert [v.verdict for v in verdicts] == ["pass", "fail", "fail"]
    assert verdicts[1].blocking == ["deformed_face"]
    # All three were judged; each vision call is charged as 5 judge units.
    assert client.calls == 3
    assert ledger.operations.get("llm_judge") == 3 * 5
    # Fetched images were base64 data URLs (real pixels passed to the judge).
    assert all(d.startswith("data:image/") for d in client.seen_data_urls)


# ---------------------------------------------------------------------------
# dead URL -> unavailable (NOT a silent pass)
# ---------------------------------------------------------------------------


def test_dead_url_is_unavailable_not_pass():
    items = [
        ImageItem(subject="Live", topic="T", image_url="http://x/live.png"),
        ImageItem(subject="Dead", topic="T", image_url="http://x/missing.png"),
        ImageItem(subject="Http500", topic="T", image_url="http://x/err.png"),
    ]
    http = FakeHttpClient(
        {
            "http://x/live.png": FakeResponse(200, _TINY_PNG),
            "http://x/err.png": FakeResponse(500, b""),
        }
    )
    client = FakeVisionClient([_good()])
    ledger = SpendLedger(cap_cents=10_000)

    verdicts = asyncio.run(
        evaluate_images(
            items,
            vision_client=client,
            judge_model="fake",
            spend_ledger=ledger,
            http_client=http,
        )
    )
    assert verdicts[0].verdict == "pass"
    assert verdicts[1].verdict == "unavailable"  # connection failure
    assert verdicts[2].verdict == "unavailable"  # HTTP 500
    # Only the live image cost a judge call (dead ones never reach the judge).
    assert client.calls == 1
    assert ledger.operations.get("llm_judge") == 5  # one vision call = 5 units


# ---------------------------------------------------------------------------
# --max-spend cap: fail-safe + remainder -> skipped-budget
# ---------------------------------------------------------------------------


def test_max_spend_cap_skips_remainder():
    items = [
        ImageItem(subject=f"C{i}", topic="T", image_url=f"http://x/{i}.png")
        for i in range(5)
    ]
    http = FakeHttpClient(
        {f"http://x/{i}.png": FakeResponse(200, _TINY_PNG) for i in range(5)}
    )
    client = FakeVisionClient([_good()] * 5)
    # Each vision call gates+charges PROJECTED_JUDGE_CENTS == 1.0 (0.2 * 5).
    # A 2-cent cap therefore allows exactly 2 calls, then skips the remainder.
    ledger = SpendLedger(cap_cents=2)

    verdicts = asyncio.run(
        evaluate_images(
            items,
            vision_client=client,
            judge_model="fake",
            spend_ledger=ledger,
            http_client=http,
        )
    )
    judged = [v for v in verdicts if v.verdict in ("pass", "fail")]
    skipped = [v for v in verdicts if v.verdict == "skipped-budget"]
    assert len(judged) == 2
    assert len(skipped) == 3
    # Never spent past the cap (fail-safe gate runs BEFORE each judge call).
    assert ledger.spent_cents <= ledger.cap_cents
    # Skipped images never hit the (paid) judge.
    assert client.calls == 2


# ---------------------------------------------------------------------------
# judge error -> verdict 'error' (not pass), still charged
# ---------------------------------------------------------------------------


def test_judge_error_is_error_not_pass():
    items = [ImageItem(subject="X", topic="T", image_url="http://x/x.png")]
    http = FakeHttpClient({"http://x/x.png": FakeResponse(200, _TINY_PNG)})
    client = FakeVisionClient([_good()], raise_on={0})
    ledger = SpendLedger(cap_cents=10_000)

    verdicts = asyncio.run(
        evaluate_images(
            items,
            vision_client=client,
            judge_model="fake",
            spend_ledger=ledger,
            http_client=http,
        )
    )
    assert verdicts[0].verdict == "error"


# ---------------------------------------------------------------------------
# aggregate + pass-rate
# ---------------------------------------------------------------------------


def test_aggregate_pass_rate_excludes_skipped_budget():
    verdicts = [
        ImageVerdict(subject="a", topic="t", fidelity=9, relevance=9, style_ok=True, verdict="pass"),
        ImageVerdict(subject="b", topic="t", verdict="fail"),
        ImageVerdict(subject="c", topic="t", verdict="unavailable"),
        ImageVerdict(subject="d", topic="t", verdict="skipped-budget"),
    ]
    ledger = SpendLedger(cap_cents=100)
    agg = aggregate(verdicts, ledger)
    # judged = pass + fail + unavailable (3), skipped excluded from denominator.
    assert agg["judged"] == 3
    assert agg["passed"] == 1
    assert agg["pass_rate"] == round(1 / 3, 4)
    assert agg["verdict_counts"]["skipped-budget"] == 1


# ---------------------------------------------------------------------------
# end-to-end CLI exit code + read-only default
# ---------------------------------------------------------------------------


def _patch_vision(monkeypatch, scores: list[VisionScore]):
    """Replace the real LiteLLM vision client with a fake (no network)."""
    holder = {"client": None}

    def _factory():
        c = FakeVisionClient(scores)
        holder["client"] = c
        return c

    monkeypatch.setattr(
        "scripts.eval_image_quality.LiteLLMVisionClient", _factory
    )
    return holder


def _write_local_images(tmp_path: Path, n: int) -> Path:
    folder = tmp_path / "imgs"
    folder.mkdir()
    subjects = {}
    for i in range(n):
        fn = f"img{i}.png"
        (folder / fn).write_bytes(_TINY_PNG)
        subjects[fn] = {"subject": f"Subject {i}", "topic": "Demo"}
    (folder / "subjects.json").write_text(json.dumps(subjects), encoding="utf-8")
    return folder


def test_cli_exit_zero_when_pass_rate_met(tmp_path: Path, monkeypatch, capsys):
    folder = _write_local_images(tmp_path, 2)
    _patch_vision(monkeypatch, [_good(), _good()])
    rc = main(["--dir", str(folder), "--min-pass-rate", "0.85", "--max-spend", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pass_rate=100.00%" in out


def test_cli_exit_one_when_pass_rate_below_floor(tmp_path: Path, monkeypatch):
    folder = _write_local_images(tmp_path, 2)
    _patch_vision(monkeypatch, [_good(), _low_fidelity()])  # 50% pass
    rc = main(["--dir", str(folder), "--min-pass-rate", "0.85", "--max-spend", "10"])
    assert rc == 1


def test_cli_read_only_by_default_no_write_scores(tmp_path: Path, monkeypatch):
    """Without --write-scores, write_scores_to_db must never be invoked."""
    folder = _write_local_images(tmp_path, 1)
    _patch_vision(monkeypatch, [_good()])

    called = {"write": False}

    async def _boom(*_a, **_k):
        called["write"] = True
        return 0

    monkeypatch.setattr("scripts.eval_image_quality.write_scores_to_db", _boom)
    rc = main(["--dir", str(folder), "--max-spend", "10"])
    assert rc == 0
    assert called["write"] is False  # read-only default honoured


def test_cli_json_report_written(tmp_path: Path, monkeypatch):
    folder = _write_local_images(tmp_path, 2)
    report = tmp_path / "report.json"
    _patch_vision(monkeypatch, [_good(), _bad_blocker()])
    main(["--dir", str(folder), "--json", str(report), "--max-spend", "10",
          "--min-pass-rate", "0.0"])
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["aggregate"]["total"] == 2
    assert len(data["results"]) == 2
    assert {r["verdict"] for r in data["results"]} == {"pass", "fail"}


# ---------------------------------------------------------------------------
# fetch helpers: local path + data URL
# ---------------------------------------------------------------------------


def test_fetch_local_path(tmp_path: Path):
    p = tmp_path / "x.png"
    p.write_bytes(_TINY_PNG)
    item = ImageItem(subject="s", image_path=str(p))
    data_url = asyncio.run(fetch_image_data_url(item, timeout_s=5))
    assert data_url is not None and data_url.startswith("data:image/png;base64,")


def test_fetch_missing_local_path_returns_none(tmp_path: Path):
    item = ImageItem(subject="s", image_path=str(tmp_path / "nope.png"))
    assert asyncio.run(fetch_image_data_url(item, timeout_s=5)) is None


def test_fetch_passthrough_data_url():
    url = "data:image/png;base64,AAAA"
    item = ImageItem(subject="s", image_url=url)
    assert asyncio.run(fetch_image_data_url(item, timeout_s=5)) == url


# ---------------------------------------------------------------------------
# tolerant JSON parser
# ---------------------------------------------------------------------------


def test_parse_vision_score_handles_fenced_json():
    text = '```json\n{"fidelity": 8, "relevance": 9, "style_ok": true, ' \
           '"blocking_reasons": [], "notes": "clean"}\n```'
    s = parse_vision_score(text)
    assert s.fidelity == 8 and s.relevance == 9 and s.style_ok is True
    assert verdict_from_score(s) == "pass"


def test_parse_vision_score_clamps_and_coerces():
    s = parse_vision_score('{"fidelity": 99, "relevance": -2, "style_ok": "yes"}')
    assert s.fidelity == 10  # clamped to 10
    assert s.relevance == 1  # clamped to 1


def test_parse_vision_score_garbage_fails_safe():
    s = parse_vision_score("not json at all")
    assert s.fidelity == 1 and s.relevance == 1 and s.style_ok is False
    assert "unparseable_judge_output" in s.blocking_reasons
    assert verdict_from_score(s) == "fail"
