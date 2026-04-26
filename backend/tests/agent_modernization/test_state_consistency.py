"""Iter 3 — guard against drift between the runtime ``GraphState`` TypedDict
and the transport ``AgentGraphStateModel`` Pydantic schema.

Both describe the same logical agent state; they MUST agree on field names so
that round-trips through Redis (using AgentGraphStateModel) preserve every
key the graph reads/writes at runtime (TypedDict).
"""

from __future__ import annotations

from app.agent.schemas import AgentGraphStateModel
from app.agent.state import GraphState


def _typed_dict_keys() -> set[str]:
    # TypedDict exposes annotations on __annotations__ at class level
    return set(GraphState.__annotations__.keys())


def _pydantic_keys() -> set[str]:
    return set(AgentGraphStateModel.model_fields.keys())


def test_pydantic_state_covers_every_typed_dict_field() -> None:
    """Every runtime field must round-trip through the transport schema."""
    missing = _typed_dict_keys() - _pydantic_keys()
    assert not missing, (
        "AgentGraphStateModel is missing fields present in GraphState "
        f"TypedDict: {sorted(missing)}. Add them to the Pydantic model so "
        "Redis round-trip does not drop runtime data."
    )


def test_typed_dict_covers_every_pydantic_field() -> None:
    """The runtime TypedDict should not omit fields the transport stores."""
    missing = _pydantic_keys() - _typed_dict_keys()
    assert not missing, (
        "GraphState TypedDict is missing fields present in "
        f"AgentGraphStateModel: {sorted(missing)}. Either add the field to "
        "the runtime state or remove it from the transport model."
    )


def test_pydantic_state_uses_strict_extra_forbid() -> None:
    """Transport model must reject unknown fields to catch drift early."""
    cfg = AgentGraphStateModel.model_config
    assert cfg.get("extra") == "forbid", (
        "AgentGraphStateModel must use extra='forbid' to surface drift early."
    )
