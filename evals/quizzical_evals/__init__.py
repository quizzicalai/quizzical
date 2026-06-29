"""Quizzical agent-evaluation framework.

A top-level, statistically grounded harness for optimizing the LangGraph agent's
per-function LLM calls in the priority order **cost -> speed -> quality**.

This package is intentionally decoupled from the FastAPI app: it imports the
agent's *prompts and schemas* (the source of truth) but talks to providers
through its own thin caller so we can pin a model per cell without touching
``appconfig.local.yaml``. See ``evals/methodology.md`` for the design.

Nothing here makes a paid LLM call unless you pass ``--live``. The default is a
deterministic ``--dry-run`` mock path so the harness, schemas, and statistics
can be exercised in CI with zero spend.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
