# tests/unit/services/test_llm_cost_metering.py
"""Live LLM spend metering coverage (punchlist #10).

The live paid pipeline records real token/$ usage off every structured LLM
response via ``cost_meter.record_llm_cost`` (a single call in a try/except-pass
inside ``LLMService._attempt_for_model``). Two gaps this file closes:

1. WRAPPER path — that the structured-call attempt, given a realistic
   ``ResponsesAPIResponse`` from a stubbed ``litellm.responses``, actually feeds
   the daily cents counter (spies on ``cost_meter.record_cents`` for cents > 0).
   The parse/validate step is stubbed so the test isolates the cost-metering
   seam, not the JSON extraction (covered elsewhere).

2. NO-NETWORK regression — that a REAL ``ResponsesAPIResponse`` (model
   ``gpt-4o-mini``, fixed usage) fed through ``record_llm_cost`` with the REAL
   ``litellm.completion_cost`` (Responses-API handling is unpinned upstream)
   still yields cents > 0. This runs entirely offline: no provider call, no key,
   no spend — it just costs out a synthetic usage block.

Both are fail-open by contract; a metering fault must never break the LLM path.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import cost_meter

pytestmark = [pytest.mark.unit]


def _make_responses_api_response(*, model: str, input_tokens: int, output_tokens: int):
    """Build a REALISTIC litellm ``ResponsesAPIResponse`` with a usage block.

    Mirrors the shape ``litellm.responses`` returns for the Responses API so both
    ``cost_meter._extract_usage`` (input/output_tokens) and
    ``litellm.completion_cost`` read it exactly as they would in production.
    """
    from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse

    usage = ResponseAPIUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
    return ResponsesAPIResponse(
        id="resp_test",
        created_at=0.0,
        model=model,
        object="response",
        output=[],
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        status="completed",
        usage=usage,
        text=None,
    )


# ---------------------------------------------------------------------------
# 1. WRAPPER: the structured attempt meters cost off a stubbed litellm.responses.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_structured_attempt_meters_cost_from_stubbed_responses(monkeypatch):
    """A structured attempt over a stubbed ``litellm.responses`` records cents > 0
    into the daily counter via the wrapper's ``record_llm_cost`` step."""
    import litellm

    from app.services import llm_service as svc

    # Realistic Responses-API response with a big-enough usage block that
    # gpt-4o-mini's real per-token rate rounds to >= 1 cent.
    resp = _make_responses_api_response(
        model="gpt-4o-mini", input_tokens=100_000, output_tokens=100_000
    )

    # Stub the network call: litellm.responses is invoked via asyncio.to_thread.
    def _fake_responses(**_payload):
        return resp

    monkeypatch.setattr(litellm, "responses", _fake_responses, raising=True)

    # Isolate the cost-metering seam from JSON parsing/validation: return a valid
    # parsed object so the attempt succeeds without coupling to the extractor.
    monkeypatch.setattr(
        svc, "_extract_structured", lambda _resp, validator=None: {"ok": True}
    )

    # Spy on the daily-counter write. record_cents is the sink record_llm_cost
    # calls once cents > 0; assert it fired with a positive integer.
    recorded: list[int] = []

    async def _spy_record_cents(_redis, cents):
        recorded.append(int(cents))
        return int(cents)

    monkeypatch.setattr(cost_meter, "record_cents", _spy_record_cents, raising=True)
    monkeypatch.setattr(cost_meter, "_get_redis_for_metering", lambda: object())

    service = svc.LLMService()
    result = await service._attempt_for_model(
        model="gpt-4o-mini",
        tool_name="next_question_generator",
        messages=[{"role": "user", "content": "hi"}],
        response_model=dict,  # trivial; parsed value is stubbed above
        trace_id="tr-1",
        session_id="sess-1",
    )

    assert result == {"ok": True}
    # The wrapper metered the real cost: record_cents fired once with cents > 0.
    assert len(recorded) == 1
    assert recorded[0] > 0


# ---------------------------------------------------------------------------
# 2. NO-NETWORK regression: REAL ResponsesAPIResponse + REAL completion_cost.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_llm_cost_real_completion_cost_no_network(monkeypatch):
    """Feed a REAL ``ResponsesAPIResponse`` through ``record_llm_cost`` using the
    REAL ``litellm.completion_cost`` (NOT monkeypatched) and assert cents > 0.

    Guards the unpinned Responses-API cost handling: if a litellm upgrade stops
    costing a Responses-API usage block, this fails. Fully offline."""
    recorded: list[int] = []

    async def _spy_record_cents(_redis, cents):
        recorded.append(int(cents))
        return int(cents)

    # ONLY stub the Redis sink + client resolution; completion_cost stays REAL.
    monkeypatch.setattr(cost_meter, "record_cents", _spy_record_cents, raising=True)
    monkeypatch.setattr(cost_meter, "_get_redis_for_metering", lambda: object())

    # 100k in + 100k out on gpt-4o-mini ≈ $0.075 -> 8 cents (well over the
    # nearest-cent rounding floor). Chosen so a rate change would have to be huge
    # to zero it out, keeping the regression stable across minor price map edits.
    resp = _make_responses_api_response(
        model="gpt-4o-mini", input_tokens=100_000, output_tokens=100_000
    )

    # Sanity: the REAL completion cost is a positive, non-trivial dollar figure.
    import litellm

    usd = litellm.completion_cost(completion_response=resp)
    assert usd is not None and usd > 0.0

    await cost_meter.record_llm_cost(
        resp,
        model="gpt-4o-mini",
        tool="next_question_generator",
        trace_id="tr",
        session_id="s",
    )

    assert len(recorded) == 1
    assert recorded[0] > 0  # real cost -> real cents on the daily breaker counter


@pytest.mark.asyncio
async def test_record_llm_cost_extracts_responses_api_usage():
    """``_extract_usage`` reads a real Responses-API usage block (input/output
    tokens), not just the Chat-Completions prompt/completion shape."""
    resp = _make_responses_api_response(
        model="gpt-4o-mini", input_tokens=1234, output_tokens=567
    )
    usage = cost_meter._extract_usage(resp)
    assert usage["input_tokens"] == 1234
    assert usage["output_tokens"] == 567
    assert usage["total_tokens"] == 1801


def test_imports_no_event_loop_warning():
    # Guard: constructing the response type must not require a running loop.
    resp = _make_responses_api_response(
        model="gpt-4o-mini", input_tokens=1, output_tokens=1
    )
    assert resp.model == "gpt-4o-mini"
    # (asyncio import kept referenced so linters don't flag it if reused above.)
    assert asyncio.iscoroutinefunction(cost_meter.record_llm_cost)
