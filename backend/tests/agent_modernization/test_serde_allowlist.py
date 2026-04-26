"""Iter 5 follow-up — register Pydantic state types with the checkpoint serde.

LangGraph 1.x emits a deprecation warning when deserializing custom types
that are not on the msgpack allowlist:

    "Deserializing unregistered type X from checkpoint. This will be blocked
    in a future version."

Our agent state contains Pydantic models (Synopsis, CharacterProfile, etc.).
The factory must register them on the saver's serializer so the next major
LangGraph release does not silently drop state.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_compiled_graph_registers_pydantic_state_modules(monkeypatch) -> None:
    from app.agent import graph as graph_mod

    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    monkeypatch.setattr(graph_mod, "_env_name", lambda: "local")

    g = await graph_mod.create_agent_graph()
    try:
        cp = g._async_checkpointer
        serde = cp.serde
        allowed = serde._allowed_msgpack_modules
        assert allowed is not True, (
            "Serializer is in permissive mode. Register the agent's Pydantic "
            "state modules explicitly via with_msgpack_allowlist to future-proof."
        )
        # The agent's state modules MUST be on the allowlist.
        for mod, name in (
            ("app.agent.schemas", "Synopsis"),
            ("app.agent.schemas", "CharacterProfile"),
            ("app.agent.schemas", "QuizQuestion"),
            ("app.agent.schemas", "QuestionAnswer"),
        ):
            assert (mod, name) in allowed, (
                f"Pydantic state type ({mod}, {name}) is not on the msgpack "
                "allowlist; future LangGraph versions will block deserialization."
            )
    finally:
        await graph_mod.aclose_agent_graph(g)
