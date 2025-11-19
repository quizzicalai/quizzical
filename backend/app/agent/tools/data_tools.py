# backend/app/agent/tools/data_tools.py
"""
Agent Tools: Data Retrieval (RAG, Web Search, DB lookups)

- Vector RAG over prior sessions (pgvector)
- Character fetch by ID
- Wikipedia search (lightweight, local)
- Web search via OpenAI **Responses API** (through our llm_service)
  - Uses the Responses API `web_search` tool
  - Runs on gpt-5-family models and honors retrieval policy/budget
  - Returns a concise plain-text synthesis

These tools are tolerant (non-blocking on failure) and log richly for diagnosis.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Retrieval policy & per-run budget (ADD-ONLY)
# ---------------------------------------------------------------------------

# In-memory budget per (trace_id|session_id). If `settings.retrieval` is absent,
# behavior is unchanged (no limits).
_RETRIEVAL_BUDGET: Dict[str, int] = {}


def _run_key(trace_id: Optional[str], session_id: Optional[str]) -> str:
    return f"{trace_id or ''}|{session_id or ''}"


def _policy_allows(kind: str, *, is_media: Optional[bool] = None) -> bool:
    """
    kind: 'web' | 'wiki'
    If retrieval config is absent, allow by default (back-compat).
    """
    r = getattr(settings, "retrieval", None)
    if not r:
        return True

    policy = (getattr(r, "policy", "off") or "off").lower()
    allow_wiki = bool(getattr(r, "allow_wikipedia", False))
    allow_web = bool(getattr(r, "allow_web", False))

    if policy == "off":
        return False
    if kind == "wiki" and not allow_wiki:
        return False
    if kind == "web" and not allow_web:
        return False
    if policy == "media_only":
        # If we don't know whether it's media, be conservative and deny.
        if is_media is False:
            return False
        if is_media is None:
            return False
    return True


def consume_retrieval_slot(trace_id: Optional[str], session_id: Optional[str]) -> bool:
    """
    Consume one retrieval slot if configured. Returns True if allowed.
    If retrieval config is absent, returns True (no limits). If max_calls_per_run<=0, returns False.
    """
    r = getattr(settings, "retrieval", None)
    if not r:
        return True  # back-compat: unlimited when not configured

    try:
        max_calls = int(getattr(r, "max_calls_per_run", 0) or 0)
    except Exception:
        max_calls = 0

    if max_calls <= 0:
        return False

    k = _run_key(trace_id, session_id)
    used = _RETRIEVAL_BUDGET.get(k, 0)
    if used >= max_calls:
        return False
    _RETRIEVAL_BUDGET[k] = used + 1
    return True


# -------------------------
# Pydantic Inputs
# -------------------------

class SynopsisInput(BaseModel):
    """Input for contextual session search."""
    synopsis: str = Field(description="Detailed synopsis for the quiz category.")

class CharacterInput(BaseModel):
    """Input for fetching character details."""
    character_id: str = Field(description="UUID of the character to fetch.")

# -------------------------
# Wikipedia (simple, local)
# -------------------------

_wikipedia_search = WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=2000)

@tool
def wikipedia_search(query: str) -> str:
    """
    Searches for a term on Wikipedia (encyclopedic info).
    Returns "" on error.

    Note: Budget enforcement is typically done by call sites (so we can
    attribute the slot to a (trace_id|session_id)). We still honor global
    policy here to block outright when disabled or budget is 0.
    """
    # ADD: coarse policy block — if configured & disallowed, short-circuit
    r = getattr(settings, "retrieval", None)
    if r:
        if not bool(getattr(r, "allow_wikipedia", False)):
            logger.info("tool.wikipedia_search.blocked_by_policy")
            return ""
        try:
            if int(getattr(r, "max_calls_per_run", 0) or 0) <= 0:
                logger.info("tool.wikipedia_search.blocked_no_budget")
                return ""
        except Exception:
            logger.info("tool.wikipedia_search.blocked_no_budget_parse")
            return ""

    logger.info("tool.wikipedia_search.start", query=query)
    try:
        result = _wikipedia_search.run(query) or ""
        logger.info("tool.wikipedia_search.ok", has_result=bool(result))
        return result
    except Exception as e:
        logger.error("tool.wikipedia_search.fail", error=str(e), exc_info=True)
        return ""

# -------------------------
# Web Search (Responses API via llm_service)
# -------------------------

@tool
async def web_search(query: str, trace_id: Optional[str] = None, session_id: Optional[str] = None) -> str:
    """
    Config-driven web search using the OpenAI Responses API `web_search` tool.
    """
    logger.info("tool.web_search.start", query=query, trace_id=trace_id, session_id=session_id)

    # 1. Policy Checks
    if not _policy_allows("web"):
        logger.info("tool.web_search.blocked_by_policy")
        return ""
    if not consume_retrieval_slot(trace_id, session_id):
        logger.info("tool.web_search.no_budget_left")
        return ""

    # 2. Config Checks
    cfg = getattr(settings, "llm_tools", {}).get("web_search")
    if not cfg:
        logger.error("tool.web_search.no_config")
        return ""

    # 3. Build Spec (Delegated)
    tool_spec = _build_web_search_spec(cfg)

    # 4. Prepare Runtime Options
    reasoning = {"effort": getattr(cfg, "effort", None)} if getattr(cfg, "effort", None) else None

    tool_choice = getattr(cfg, "tool_choice", None)
    if not tool_choice or not isinstance(tool_choice, (str, dict)):
        tool_choice = "auto"

    messages = [
        {
            "role": "system",
            "content": (
                "You are a web research assistant. Use the web_search tool if available. "
                "Return a concise synthesis. If the tool returns sources, list 3–5 at the end."
            ),
        },
        {"role": "user", "content": query},
    ]

    # 5. Execution
    try:
        provider = getattr(getattr(settings, "llm", object()), "provider", "openai")
        extra_opts: Dict[str, Any] = {}
        size = getattr(cfg, "search_context_size", None)
        if size and str(provider).lower() != "openai":
            extra_opts["web_search_options"] = {"search_context_size": size}

        out = await llm_service.get_text(
            messages,
            model=getattr(cfg, "model", None),
            tools=[tool_spec],
            tool_choice=tool_choice,
            reasoning=reasoning,
            metadata={"trace_id": trace_id, "session_id": session_id},
            **extra_opts,
        )
        text_out = (out or "").strip() if isinstance(out, str) else ""
        logger.info("tool.web_search.done", has_text=bool(text_out))
        return text_out
    except Exception as e:
        logger.error("tool.web_search.api_error", error=str(e), exc_info=True)
        return ""

def _build_web_search_spec(cfg: Any) -> Dict[str, Any]:
    """Helper to build the complex tool specification dict."""
    provider = getattr(getattr(settings, "llm", object()), "provider", "openai")
    tool_type = "web_search" if str(provider).lower() == "openai" else "web_search_preview"
    tool_spec: Dict[str, Any] = {"type": tool_type}

    # Domain filters logic
    r = _get_retrieval_settings()
    cfg_domains = list(getattr(cfg, "allowed_domains", []) or [])
    override_domains = list(getattr(r, "allowed_domains", []) or []) if r else []
    effective_domains = override_domains or cfg_domains

    if effective_domains:
        def _clean(d: str) -> str:
            d = (d or "").strip().removeprefix("https://").removeprefix("http://")
            return d[:-1] if d.endswith("/") else d

        cleaned = [_clean(d) for d in effective_domains if d]
        if cleaned:
            tool_spec["filters"] = {"allowed_domains": cleaned[:20]}

    # User location logic
    if getattr(cfg, "user_location", None):
        try:
            tool_spec["user_location"] = {
                "type": "approximate",
                **{k: v for k, v in cfg.user_location.model_dump().items() if v}
            }
        except Exception:
            pass

    return tool_spec

def _get_retrieval_settings() -> Any:
    """Safe getter for retrieval settings."""
    return getattr(settings, "retrieval", None)
