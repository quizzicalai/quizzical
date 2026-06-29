"""Robust JSON extraction from model text (fence-stripping + balanced scan).

Mirrors the proven logic in ``backend/app/services/llm_service.py`` and
``backend/Analysis/llm_caller.py`` so eval parsing matches production parsing.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)


def _strip_fences(s: str) -> str:
    s = s.strip()
    m = _FENCE_RE.search(s)
    return m.group(1).strip() if m else s


def _first_balanced(s: str) -> str | None:
    s = s.strip()
    if not s:
        return None
    if s[0] in "[{":
        return s
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return s[start : i + 1]
    return None


def parse_json_loose(text: str) -> Any:
    """Parse JSON from arbitrary model output. Raises ValueError on total failure."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty model output")
    for cand in (text, _strip_fences(text), _first_balanced(text)):
        if not cand:
            continue
        try:
            return json.loads(cand)
        except Exception:
            continue
    raise ValueError("could not parse JSON from model output")
