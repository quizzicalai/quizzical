"""Config and result schemas for the eval framework.

Two halves:

1. **Config** -- what we sweep. A ``FunctionEvalSpec`` describes ONE agent
   function (e.g. ``next_question_generator``) plus the set of ``ConfigVariant``s
   (model + prompt + tool-knob combinations) to compare on a shared, pinned
   input dataset. Loaded from ``evals/config/*.yaml``.

2. **Results** -- what we record. ``CellResult`` is one (variant x input x rep)
   observation carrying cost, latency, and quality. These are appended to a
   JSONL results file and consumed by ``stats.py`` / ``report.py``.

The config maps 1:1 onto the agent's tool registry in
``backend/app/agent/schemas.py`` (``SCHEMA_REGISTRY`` / ``JSONSCHEMA_REGISTRY``)
and the prompt registry in ``backend/app/agent/prompts.py`` so a variant can
faithfully reproduce a production call.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ===========================================================================
# Config side
# ===========================================================================


@dataclass(frozen=True)
class ConfigVariant:
    """One point in the search space for a function: a concrete call config.

    A variant pins everything that could change cost/speed/quality:
      * ``model``            -- litellm model id (cost & speed driver)
      * ``prompt_strategy``  -- which prompt variant (see prompts_adapter); maps
                                to Analysis/prompts_variants.py strategies
                                ("baseline" | "cot" | "fewshot") plus any new ones
      * ``max_output_tokens``/``temperature``/``timeout_s``/``effort`` -- knobs
                                that the production ``invoke_structured`` reads
                                from per-tool config.
    """

    name: str
    model: str
    prompt_strategy: str = "baseline"
    max_output_tokens: int | None = None
    temperature: float | None = None
    timeout_s: int | None = None
    effort: str | None = None  # reasoning effort: low|medium|high (reasoning models)
    notes: str = ""

    def call_kwargs(self) -> dict[str, Any]:
        """Knobs to forward to the LLM caller, dropping unset (None) values."""
        out: dict[str, Any] = {"model": self.model}
        if self.max_output_tokens is not None:
            out["max_output_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            out["temperature"] = self.temperature
        if self.timeout_s is not None:
            out["timeout_s"] = self.timeout_s
        if self.effort is not None:
            out["effort"] = self.effort
        return out


@dataclass(frozen=True)
class QualityFloor:
    """A minimum acceptable quality bar for a function (the cost-first gate).

    The decision rule is *lexicographic*: among variants whose quality CI lower
    bound clears ``min_mean_score`` (and that satisfy ``deterministic_checks``),
    pick the cheapest; break ties by latency. See decision.py.
    """

    min_mean_score: float = 4.0  # on the judge's 1-5 Likert scale
    # name of a deterministic-check function (in checks.py) that must pass for
    # an output to be eligible; empty = no hard deterministic gate.
    required_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class FunctionEvalSpec:
    """Everything needed to evaluate one agent function."""

    function: str  # tool_name, e.g. "next_question_generator"
    description: str
    response_schema: str  # tool_name passed to schemas.jsonschema_for(...)
    dataset: str  # path (relative to evals/) to the input fixture JSON
    variants: tuple[ConfigVariant, ...]
    quality_floor: QualityFloor = field(default_factory=QualityFloor)
    # judge dimensions (subset of judge rubric) that score THIS function.
    judge_dimensions: tuple[str, ...] = ()
    # deterministic checks always computed for diagnostics (not necessarily gates)
    deterministic_checks: tuple[str, ...] = ()
    latency_budget_p95_s: float | None = None  # speed gate (wall-clock p95)


# ===========================================================================
# Result side
# ===========================================================================


@dataclass
class CellResult:
    """One observation: (function, variant, input_id, rep).

    Carries the three metric families. ``error`` is non-None when the call or
    parse failed; such rows are excluded from quality stats but COUNTED for a
    reliability/validity rate (a config that is cheap+fast but fails 30% of the
    time must lose).
    """

    function: str
    variant: str
    model: str
    prompt_strategy: str
    input_id: str
    rep: int

    # cost
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    # speed (seconds)
    latency_wall_s: float = 0.0
    latency_ttft_s: float | None = None  # time-to-first-token (streaming only)

    # quality
    judge_scores: dict[str, float] = field(default_factory=dict)
    judge_agg: float | None = None
    check_results: dict[str, bool] = field(default_factory=dict)
    valid_output: bool = True  # schema-valid + parsed

    error: str | None = None
    dry_run: bool = False

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CellResult":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def load_results(path: str | Path) -> list[CellResult]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[CellResult] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(CellResult.from_dict(json.loads(line)))
        except Exception:
            continue
    return rows
