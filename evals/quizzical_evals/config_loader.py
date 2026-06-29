"""Load ``FunctionEvalSpec`` objects from ``evals/config/*.yaml``.

A config file describes one function and its variant sweep. Example shape::

    function: next_question_generator
    description: "Adaptive per-question generator (prod loop hotspot)."
    response_schema: next_question_generator
    dataset: datasets/next_question_generator.json
    quality_floor:
      min_mean_score: 4.0
      required_checks: [options_count_ok, questions_unique]
    judge_dimensions: [baseline_quality, answer_option_quality]
    deterministic_checks: [options_count_ok, questions_unique, options_do_not_leak_outcomes]
    latency_budget_p95_s: 8.0
    variants:
      - {name: prod_4o_mini, model: gpt-4o-mini, prompt_strategy: baseline,
         max_output_tokens: 1500, temperature: 0.25, timeout_s: 20}
      - {name: flash_lite,   model: gemini/gemini-2.5-flash-lite, ...}

PyYAML is already a backend dependency.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import ConfigVariant, FunctionEvalSpec, QualityFloor

_EVALS_ROOT = Path(__file__).resolve().parents[1]


def load_spec(path: str | Path) -> FunctionEvalSpec:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    qf_raw = raw.get("quality_floor") or {}
    quality_floor = QualityFloor(
        min_mean_score=float(qf_raw.get("min_mean_score", 4.0)),
        required_checks=tuple(qf_raw.get("required_checks", [])),
    )
    variants = tuple(
        ConfigVariant(
            name=v["name"],
            model=v["model"],
            prompt_strategy=v.get("prompt_strategy", "baseline"),
            max_output_tokens=v.get("max_output_tokens"),
            temperature=v.get("temperature"),
            timeout_s=v.get("timeout_s"),
            effort=v.get("effort"),
            notes=v.get("notes", ""),
        )
        for v in raw["variants"]
    )
    return FunctionEvalSpec(
        function=raw["function"],
        description=raw.get("description", ""),
        response_schema=raw.get("response_schema", raw["function"]),
        dataset=raw["dataset"],
        variants=variants,
        quality_floor=quality_floor,
        judge_dimensions=tuple(raw.get("judge_dimensions", [])),
        deterministic_checks=tuple(raw.get("deterministic_checks", [])),
        latency_budget_p95_s=raw.get("latency_budget_p95_s"),
    )


def load_all_specs(config_dir: str | Path = "config") -> list[FunctionEvalSpec]:
    d = _EVALS_ROOT / config_dir if not Path(config_dir).is_absolute() else Path(config_dir)
    return [load_spec(p) for p in sorted(d.glob("*.yaml"))]
