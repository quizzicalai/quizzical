# tests/unit/services/test_result_image_judge.py
"""LIVE result-image quality gate (owner finding #3, 2026-07-02).

`generate_result_image` now judges the rendered pixels with the shared vision
judge (`app.services.vision_judge`) right after FAL returns:

  * below-bar first render  -> ONE retry with a strengthened prompt that folds
    the judge's failure reason back in -> the better-scoring render persists;
  * judge unavailable/error -> FAIL-OPEN: the first render is accepted as-is
    and NO paid retry is spent;
  * gate disabled           -> byte-for-byte legacy behaviour (no judge calls).

All tests use a FAKE judge client + FAKE FAL client — no network, no LLM.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.vision_judge import VisionScore

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeJudgeClient:
    """Queued VisionScores per call; can raise on selected call indices."""

    def __init__(self, scores: list[VisionScore], raise_on: set[int] | None = None):
        self._scores = list(scores)
        self._raise_on = raise_on or set()
        self.calls: list[dict] = []

    async def score(self, **kwargs):
        idx = len(self.calls)
        self.calls.append(kwargs)
        if idx in self._raise_on:
            raise RuntimeError("simulated judge outage")
        return self._scores[idx] if idx < len(self._scores) else self._scores[-1]


def _pass_score() -> VisionScore:
    return VisionScore(fidelity=9, relevance=9, style_ok=True, blocking_reasons=[])


def _fail_score(notes: str = "subject looks deformed") -> VisionScore:
    return VisionScore(
        fidelity=3,
        relevance=4,
        style_ok=True,
        blocking_reasons=["deformed_face"],
        notes=notes,
    )


def _mediocre_score() -> VisionScore:
    # No blockers but below the bar of 7.
    return VisionScore(fidelity=5, relevance=6, style_ok=True, blocking_reasons=[])


@pytest.fixture
def result():
    from app.models.api import FinalResult

    return FinalResult(title="The Bridge Troll", description="Gruff but loyal.")


@pytest.fixture
def pipeline(monkeypatch):
    """Wire the pipeline with fakes; returns a mutable test harness namespace."""
    from app.services import image_pipeline as ip

    state = {
        "gen_calls": [],       # list of (prompt, kwargs)
        "gen_urls": ["https://x/first.png", "https://x/retry.png"],
        "persisted": [],       # urls passed to _persist_result_image
        "judge": None,         # FakeJudgeClient, set per-test
        "spend_calls": 0,
    }

    async def _gen(prompt, **kw):
        state["gen_calls"].append((prompt, kw))
        idx = len(state["gen_calls"]) - 1
        urls = state["gen_urls"]
        return urls[idx] if idx < len(urls) else urls[-1]

    async def _persist(*, session_id, url):
        state["persisted"].append(url)

    async def _spend(*a, **k):
        state["spend_calls"] += 1

    async def _fake_to_data_url(**kwargs):
        return "data:image/png;base64,AAAA"

    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)
    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_result_image", _persist, raising=False)
    monkeypatch.setattr(ip, "_record_image_spend", _spend, raising=False)
    monkeypatch.setattr(ip, "_result_judge_enabled", lambda: True, raising=False)
    monkeypatch.setattr(ip, "_result_judge_min_score", lambda: 7, raising=False)
    monkeypatch.setattr(
        ip, "_make_result_judge_client", lambda: state["judge"], raising=False
    )
    # The judge fetches the rendered bytes before scoring; stub the fetch so no
    # network is touched (the client fake receives the canned data URL).
    monkeypatch.setattr(
        "app.services.vision_judge.to_data_url", _fake_to_data_url, raising=True
    )
    return state


async def _run(result, state):
    from app.services import image_pipeline as ip

    return await ip.generate_result_image(
        session_id=uuid4(),
        result=result,
        category="Which fairy tale creature are you?",
        character_set=[],
        analysis={},
    )


# ---------------------------------------------------------------------------
# Pass on the first render — no retry, no extra spend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_first_render_no_retry(pipeline, result):
    pipeline["judge"] = FakeJudgeClient([_pass_score()])

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]
    assert len(pipeline["gen_calls"]) == 1          # no paid retry
    assert len(pipeline["judge"].calls) == 1
    assert pipeline["spend_calls"] == 1             # only the first render metered


# ---------------------------------------------------------------------------
# Below bar -> ONE strengthened retry -> best-of accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_below_bar_retries_with_strengthened_prompt_and_accepts_better(
    pipeline, result
):
    pipeline["judge"] = FakeJudgeClient([_fail_score("subject looks deformed"), _pass_score()])

    url = await _run(result, pipeline)

    # The retry render won best-of and is what persists.
    assert url == "https://x/retry.png"
    assert pipeline["persisted"] == ["https://x/retry.png"]
    assert len(pipeline["gen_calls"]) == 2
    assert pipeline["spend_calls"] == 2             # both paid renders metered

    # The retry prompt is STRENGTHENED: it folds in the judge's failure reason
    # (blocking reason + notes) and re-asserts the subject.
    retry_prompt = pipeline["gen_calls"][1][0]
    first_prompt = pipeline["gen_calls"][0][0]
    assert retry_prompt != first_prompt
    assert first_prompt in retry_prompt             # additive correction clause
    assert "deformed_face" in retry_prompt
    assert "subject looks deformed" in retry_prompt
    assert "The Bridge Troll" in retry_prompt

    # A DIFFERENT seed on purpose (the first seed produced the bad render).
    assert pipeline["gen_calls"][1][1].get("seed") != pipeline["gen_calls"][0][1].get("seed")


@pytest.mark.asyncio
async def test_below_bar_retry_scores_worse_keeps_first(pipeline, result):
    # First render mediocre (5/6, no blockers); retry is judged WORSE (3/4 +
    # blocker). Best-of must keep the FIRST render.
    pipeline["judge"] = FakeJudgeClient([_mediocre_score(), _fail_score()])

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]
    assert len(pipeline["gen_calls"]) == 2          # the retry was attempted


@pytest.mark.asyncio
async def test_tie_keeps_first_render(pipeline, result):
    # Identical verdicts -> stable UX: keep the first render.
    pipeline["judge"] = FakeJudgeClient([_mediocre_score(), _mediocre_score()])

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]


# ---------------------------------------------------------------------------
# Fail-open paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_error_fails_open_accepts_first_and_spends_nothing_extra(
    pipeline, result
):
    pipeline["judge"] = FakeJudgeClient([_pass_score()], raise_on={0})

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]
    assert len(pipeline["gen_calls"]) == 1          # NO paid retry on judge outage
    assert pipeline["spend_calls"] == 1


@pytest.mark.asyncio
async def test_image_fetch_failure_fails_open(pipeline, result, monkeypatch):
    # The judge never sees the pixels (fetch -> None) => fail-open, no retry.
    async def _no_bytes(**kwargs):
        return None

    monkeypatch.setattr(
        "app.services.vision_judge.to_data_url", _no_bytes, raising=True
    )
    pipeline["judge"] = FakeJudgeClient([_pass_score()])

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]
    assert pipeline["judge"].calls == []            # judge never invoked
    assert len(pipeline["gen_calls"]) == 1


@pytest.mark.asyncio
async def test_retry_unjudged_accepts_corrected_render(pipeline, result):
    # First render judged below-bar; the judge errors on the SECOND call. The
    # retry was generated against the judge's specific objections, so with no
    # second verdict the corrected render is the better bet.
    pipeline["judge"] = FakeJudgeClient([_fail_score()], raise_on={1})

    url = await _run(result, pipeline)

    assert url == "https://x/retry.png"
    assert pipeline["persisted"] == ["https://x/retry.png"]
    assert len(pipeline["gen_calls"]) == 2


@pytest.mark.asyncio
async def test_retry_generate_returns_none_keeps_first(pipeline, result):
    pipeline["gen_urls"] = ["https://x/first.png", None]
    pipeline["judge"] = FakeJudgeClient([_fail_score()])

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]


# ---------------------------------------------------------------------------
# Gate disabled -> legacy behaviour, zero judge involvement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_disabled_no_judge_calls(pipeline, result, monkeypatch):
    from app.services import image_pipeline as ip

    monkeypatch.setattr(ip, "_result_judge_enabled", lambda: False, raising=False)
    pipeline["judge"] = FakeJudgeClient([_fail_score()])  # would fail if consulted

    url = await _run(result, pipeline)

    assert url == "https://x/first.png"
    assert pipeline["persisted"] == ["https://x/first.png"]
    assert pipeline["judge"].calls == []
    assert len(pipeline["gen_calls"]) == 1


# ---------------------------------------------------------------------------
# Config plumbing (defaults + validation)
# ---------------------------------------------------------------------------


def test_images_config_defaults_gate_on():
    from app.core.config import ImagesConfig

    cfg = ImagesConfig()
    assert cfg.result_judge_enabled is True
    assert cfg.result_judge_min_score == 7
    assert "gemini" in cfg.result_judge_model
    assert cfg.result_judge_timeout_s > 0


def test_images_config_rejects_out_of_range_min_score():
    from pydantic import ValidationError

    from app.core.config import ImagesConfig

    with pytest.raises(ValidationError):
        ImagesConfig(result_judge_min_score=0)
    with pytest.raises(ValidationError):
        ImagesConfig(result_judge_min_score=11)


def test_pass_rule_ignores_style_but_respects_blockers():
    from app.services import image_pipeline as ip

    # style_ok=False alone must NOT trigger a paid retry...
    ok_but_ugly = VisionScore(
        fidelity=8, relevance=8, style_ok=False, blocking_reasons=[]
    )
    assert ip._result_judge_passes(ok_but_ugly, 7) is True
    # ...but a hard blocker always fails, even with perfect scores.
    blocked = VisionScore(
        fidelity=10, relevance=10, style_ok=True, blocking_reasons=["off_topic"]
    )
    assert ip._result_judge_passes(blocked, 7) is False


def test_score_rank_prefers_blocker_free_then_weaker_axis():
    from app.services import image_pipeline as ip

    clean_low = VisionScore(fidelity=5, relevance=5, style_ok=True, blocking_reasons=[])
    blocked_high = VisionScore(
        fidelity=9, relevance=9, style_ok=True, blocking_reasons=["text_garbage"]
    )
    assert ip._score_rank(clean_low) > ip._score_rank(blocked_high)
