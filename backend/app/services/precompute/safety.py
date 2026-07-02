"""§21 Phase 3 — prompt-injection guards (security, not content safety).

Home-grown content-safety filtering and topic-policy moderation were removed
by owner decision: third-party providers (OpenAI, Google/Gemini, fal.ai) are
solely responsible for content safety. This module now contains ONLY the
prompt-injection defenses that keep user / retrieved text from escaping into
control flow:

- `wrap_user_input(text)` — produce a delimited block that is safe to
  splice into a USER message (`AC-PRECOMP-SEC-2`).
- `wrap_retrieved_block(text)` — same idea for web-retrieval snippets so
  the model is instructed to treat them as data, not control flow
  (`AC-PRECOMP-SEC-2`).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Delimited input wrappers
# ---------------------------------------------------------------------------

USER_INPUT_OPEN = "<user_input>"
USER_INPUT_CLOSE = "</user_input>"
RETRIEVED_OPEN = "<retrieved>"
RETRIEVED_CLOSE = "</retrieved>"


def _neutralize_markers(text: str, *, open_tag: str, close_tag: str) -> str:
    """Defang any nested instances of the framing tags so an attacker
    cannot escape the block by including a literal closer in their input.

    We replace the two bracket characters with their HTML-entity form;
    this makes the marker harmless to the model (which sees `&lt;…&gt;`)
    and round-trips losslessly through JSON / DB storage.
    """
    if not text:
        return ""
    safe = text
    for marker in (open_tag, close_tag):
        safe = safe.replace(marker, marker.replace("<", "&lt;").replace(">", "&gt;"))
    return safe


def wrap_user_input(raw_text: str | None) -> str:
    """Wrap user-supplied category text in a delimited block. Callers MUST
    splice this output ONLY into a USER role message; a system-prompt template
    that includes user input is a violation even with this helper.
    """
    safe = _neutralize_markers(raw_text or "", open_tag=USER_INPUT_OPEN,
                                close_tag=USER_INPUT_CLOSE)
    return f"{USER_INPUT_OPEN}\n{safe}\n{USER_INPUT_CLOSE}"


def wrap_retrieved_block(snippet: str | None, *, source_url: str | None = None) -> str:
    """`AC-PRECOMP-SEC-2` — wrap web-retrieved snippets in a delimited
    block. The system prompt that consumes this block tells the model
    to treat its contents strictly as data."""
    safe = _neutralize_markers(snippet or "", open_tag=RETRIEVED_OPEN,
                                close_tag=RETRIEVED_CLOSE)
    src_attr = f' source="{source_url}"' if source_url else ""
    return f"{RETRIEVED_OPEN[:-1]}{src_attr}>\n{safe}\n{RETRIEVED_CLOSE}"
