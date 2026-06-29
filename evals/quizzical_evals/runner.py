"""Execute (variant x input x rep) cells and record CellResults.

Flow for one cell:
    1. assemble prompt context from the dataset record (datasets.py)
    2. render the production prompt for (function, strategy) (prompts_adapter.py)
    3. call the model (caller.py: MockCaller offline / LiveCaller with --live)
    4. capture tokens -> cost (pricing.py) and wall latency
    5. run deterministic checks (checks.py)
    6. judge quality on the function's dimensions (judges.py)
    7. write one CellResult JSONL row (schema.py)

Concurrency is bounded by a semaphore (mirrors Analysis/run_experiment._bounded).
Repeats use distinct seeds so the offline mock produces *variance* (otherwise
every rep would be identical and the stats would be degenerate); the live path
gets variance for free from sampling temperature.

The runner NEVER makes a paid call unless ``live=True``. With ``live=False`` it
is fully deterministic and free, suitable for CI.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

from .caller import Caller, MockCaller
from .checks import run_checks
from .datasets import assemble_context, load_dataset
from .judges import FUNCTION_DIMENSIONS, JudgeResult, default_judge_model, judge_artifact, make_judge_caller
from .pricing import estimate_cost
from .prompts_adapter import get_prompt_pair
from .schema import CellResult, ConfigVariant, FunctionEvalSpec


def _preview(obj: object, limit: int = 1500) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)[:limit]
    except Exception:
        return str(obj)[:limit]


async def run_cell(
    *,
    spec: FunctionEvalSpec,
    variant: ConfigVariant,
    record: dict,
    rep: int,
    caller: Caller,
    judge_caller: Caller,
    judge_models: tuple[str, ...],
    live: bool,
) -> CellResult:
    input_id = str(record.get("input_id", record.get("category", "?")))
    res = CellResult(
        function=spec.function,
        variant=variant.name,
        model=variant.model,
        prompt_strategy=variant.prompt_strategy,
        input_id=input_id,
        rep=rep,
        dry_run=not live,
    )

    ctx = assemble_context(spec.function, record)
    try:
        system, user_t = get_prompt_pair(spec.function, variant.prompt_strategy)
        # Only pass keys the template references to avoid KeyError on extras.
        user = _safe_format(user_t, ctx)
        system = _safe_format(system, ctx)
    except Exception as exc:
        res.error = f"prompt_render: {exc}"
        res.valid_output = False
        return res

    kw = variant.call_kwargs()
    out = await caller.call_json(
        tool_name=spec.function,
        system=system,
        user=user,
        model=variant.model,
        max_output_tokens=kw.get("max_output_tokens", 1500),
        temperature=kw.get("temperature", 0.3),
        timeout_s=kw.get("timeout_s", 60),
        effort=kw.get("effort"),
    )

    # Cost + speed (always recorded, even on parse failure -- a failed paid call
    # still costs money and a config that fails a lot must be penalised).
    res.prompt_tokens = out.usage.prompt_tokens
    res.completion_tokens = out.usage.completion_tokens
    res.reasoning_tokens = out.usage.reasoning_tokens
    res.cost_usd = estimate_cost(variant.model, out.usage)
    res.latency_wall_s = out.latency_wall_s
    res.latency_ttft_s = out.latency_ttft_s if hasattr(out, "latency_ttft_s") else None

    if not out.ok or out.parsed is None:
        res.valid_output = False
        res.error = out.error or "no_output"
        return res

    # Deterministic checks (diagnostics + gates).
    check_ctx = dict(ctx)
    check_ctx["character_names"] = record.get("character_names") or record.get("canonical_names") or []
    res.check_results = run_checks(spec.deterministic_checks, out.parsed, check_ctx)

    # Quality judge (only for functions with judge dimensions).
    dims = spec.judge_dimensions or FUNCTION_DIMENSIONS.get(spec.function, ())
    if dims:
        jr: JudgeResult = await judge_artifact(
            judge_caller,
            function=spec.function,
            dimensions=dims,
            category=record.get("category", ""),
            bucket=record.get("bucket", "open"),
            canonical=record.get("canonical_names"),
            artifact_preview=_preview(out.parsed),
            judge_models=judge_models,
        )
        res.judge_scores = jr.scores
        res.judge_agg = jr.agg
        if jr.error:
            res.error = f"judge: {jr.error}"
    return res


async def run_spec(
    spec: FunctionEvalSpec,
    *,
    reps: int,
    caller: Caller,
    judge_caller: Caller,
    judge_models: tuple[str, ...],
    live: bool,
    concurrency: int,
    out_fp,
) -> list[CellResult]:
    records = load_dataset(spec.dataset)
    sem = asyncio.Semaphore(concurrency)
    results: list[CellResult] = []
    lock = asyncio.Lock()

    async def _one(variant: ConfigVariant, record: dict, rep: int) -> None:
        async with sem:
            r = await run_cell(
                spec=spec, variant=variant, record=record, rep=rep,
                caller=caller, judge_caller=judge_caller,
                judge_models=judge_models, live=live,
            )
        async with lock:
            out_fp.write(r.to_jsonl() + "\n")
            out_fp.flush()
            results.append(r)

    tasks = [
        _one(v, rec, rep)
        for v in spec.variants
        for rec in records
        for rep in range(reps)
    ]
    await asyncio.gather(*tasks)
    return results


async def run_all(
    specs: list[FunctionEvalSpec],
    *,
    reps: int,
    live: bool,
    concurrency: int,
    results_path: str | Path,
    judge_models: tuple[str, ...] | None = None,
) -> list[CellResult]:
    from .caller import LiveCaller

    caller: Caller = LiveCaller() if live else MockCaller()
    judge_caller: Caller = make_judge_caller(live)
    jms = judge_models or (default_judge_model(),)
    all_results: list[CellResult] = []
    Path(results_path).parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as fp:
        for spec in specs:
            rs = await run_spec(
                spec, reps=reps, caller=caller, judge_caller=judge_caller,
                judge_models=jms, live=live, concurrency=concurrency, out_fp=fp,
            )
            all_results.extend(rs)
    return all_results


def _safe_format(template: str, ctx: dict) -> str:
    """``str.format`` that tolerates missing keys (leaves placeholders intact).

    Production prompts contain literal ``{{ }}`` JSON braces (already escaped) and
    a known set of single-brace placeholders. We use a defaultdict-like mapping
    so an unexpected placeholder doesn't crash a sweep.
    """
    class _Safe(dict):
        def __missing__(self, key):  # noqa: D401
            return "{" + key + "}"

    try:
        return template.format_map(_Safe(ctx))
    except Exception:
        return template


# Convenience for ``CellResult`` -> plain dict in tests/reporting.
def as_dict(r: CellResult) -> dict:
    return dataclasses.asdict(r)
