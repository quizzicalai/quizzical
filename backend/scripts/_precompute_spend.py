"""Cumulative spend tracker with hard cap for the precompute draft loop.

Cost model (estimates, conservative — overshoots are safer than overruns):

- gemini-flash-latest text call (in/out): ~$0.005 per call (assumes ~3K
  output tokens worst case at $0.30 / 1M)
- FAL.ai flux/schnell image: ~$0.011 per image

The ledger is a simple integer-cents counter. Use ``charge_*`` helpers
to record an operation and ``would_exceed`` to gate further work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Cost constants (cents). Keep conservative.
COST_LLM_TEXT_CALL_CENTS: float = 0.5  # $0.005
COST_LLM_JUDGE_CALL_CENTS: float = 0.2  # $0.002 — judge prompts are smaller
COST_FAL_IMAGE_CENTS: float = 1.1  # $0.011

OperationKind = Literal["llm_text", "llm_judge", "fal_image"]


@dataclass
class SpendLedger:
    cap_cents: int  # hard cap; 0 disables enforcement
    spent_cents: float = 0.0
    operations: dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.spent_cents = 0.0
        self.operations.clear()

    def charge(self, kind: OperationKind, count: int = 1) -> None:
        if count <= 0:
            return
        per_unit = self._per_unit_cost(kind)
        self.spent_cents += per_unit * count
        self.operations[kind] = self.operations.get(kind, 0) + count

    def charge_llm_text(self, calls: int = 1) -> None:
        self.charge("llm_text", calls)

    def charge_llm_judge(self, calls: int = 1) -> None:
        self.charge("llm_judge", calls)

    def charge_fal_image(self, images: int = 1) -> None:
        self.charge("fal_image", images)

    def would_exceed(self, projected_cents: float) -> bool:
        if self.cap_cents <= 0:
            return False
        return (self.spent_cents + projected_cents) > self.cap_cents

    @property
    def spent_usd(self) -> float:
        return round(self.spent_cents / 100.0, 4)

    @property
    def cap_usd(self) -> float:
        return round(self.cap_cents / 100.0, 2)

    def snapshot(self) -> dict[str, object]:
        return {
            "spent_usd": self.spent_usd,
            "cap_usd": self.cap_usd,
            "operations": dict(self.operations),
        }

    @staticmethod
    def _per_unit_cost(kind: OperationKind) -> float:
        if kind == "llm_text":
            return COST_LLM_TEXT_CALL_CENTS
        if kind == "llm_judge":
            return COST_LLM_JUDGE_CALL_CENTS
        if kind == "fal_image":
            return COST_FAL_IMAGE_CENTS
        raise ValueError(f"unknown operation kind: {kind}")


def estimate_topic_text_cost_cents() -> float:
    """Roughly: 4 LLM calls per topic (analyze + plan + chars + questions)."""
    return COST_LLM_TEXT_CALL_CENTS * 4


def estimate_topic_judge_cost_cents() -> float:
    """Two-judge consensus = 2 calls per topic."""
    return COST_LLM_JUDGE_CALL_CENTS * 2


def estimate_topic_image_cost_cents(num_chars: int) -> float:
    return COST_FAL_IMAGE_CENTS * max(0, num_chars)
