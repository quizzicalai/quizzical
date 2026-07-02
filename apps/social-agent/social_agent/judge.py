"""The quality gate: strong-judge prompts + defensive verdict parsing.

Owner requirements this module encodes:
- An agent evaluates quality, uniqueness, relevance, and conscientiousness
  before ANYTHING is posted. Small models are poor judges of "silly vs
  insensitive", so the judge model is gpt-4o-class (see config.JUDGE_MODEL).
- Double-check: relevant, conscientious, on-brand (fun + silly), and — for
  replies — the right "nature" of the target post.
- REFUSE BY DEFAULT: any parse failure, missing field, or uncertainty is a
  rejection. A post that never goes out is a non-event; a bad post is a brand
  incident.

Prompt-building and parsing are pure (stdlib-only) so they unit-test without
the app venv; the actual LLM call lives in llm.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

APPROVE_MIN_QUALITY = 7  # judge score floor, 1-10

JUDGE_SYSTEM_PROMPT = """\
You are the final publication gate for the X (Twitter) account of quafel — a playful AI
personality-quiz site (brand always lowercase: "quafel"). Nothing is posted unless YOU approve it.

Brand voice: short, silly, absurd, self-deprecating, warm. Never mean, never punching down,
never political, never crude, never salesy. Think "I was today years old when I discovered my
personality is homemade mac-n-cheese".

For EACH candidate you receive, evaluate:
- quality (1-10): is it genuinely witty and fun? Flat, try-hard, or cringe = low score.
- on_brand: silly + fun + short, "quafel" lowercase if mentioned, no hashtag spam.
- conscientious: could this land as insensitive, mocking, emotionally naive, or tone-deaf to
  ANY plausible reader? For replies, judge the NATURE of the post being replied to: if the
  author is venting, grieving, unwell, discussing politics/identity, being sincere about
  something tender, or would plausibly not welcome a joke — REJECT. Jokes are only for posts
  that are clearly lighthearted and receptive.
- relevant (replies only): does the reply actually engage with what the target said, rather
  than being a generic bolt-on ad? For standalone posts set relevant to true.

BE STRICT. When uncertain on ANY dimension, REJECT — refusing is free, a bad post is not.

Respond with ONLY a JSON object: {"verdicts": [{"index": <int>, "approve": <bool>,
"quality": <int 1-10>, "on_brand": <bool>, "conscientious": <bool>, "relevant": <bool>,
"reason": "<short reason>"}, ...]} — one verdict per candidate, no other text.
"""


def build_judge_user_prompt(
    candidates: list[dict[str, Any]],
    kind: str,
) -> str:
    """Build the user message for a batch of candidates.

    For kind='reply' each candidate dict must include 'target_text' (and
    ideally 'target_author'), so the judge can weigh the nature of the target.
    """
    lines: list[str] = []
    if kind == "reply":
        lines.append(
            "Candidates are REPLIES. For each, first read the target post and decide whether a "
            "silly quafel reply is welcome there AT ALL; then judge the reply text itself."
        )
    else:
        lines.append(
            "Candidates are STANDALONE PROFILE POSTS (a fake personality-quiz result with a share "
            "link placeholder {link})."
        )
    for i, c in enumerate(candidates):
        lines.append(f"--- candidate {i} ---")
        if kind == "reply":
            author = c.get("target_author") or "unknown"
            lines.append(f"target post (by @{author}): {c.get('target_text', '')!r}")
        lines.append(f"proposed text: {c.get('text', '')!r}")
    return "\n".join(lines)


@dataclass
class JudgeVerdict:
    index: int
    approve: bool
    quality: int
    on_brand: bool
    conscientious: bool
    relevant: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "approve": self.approve,
            "quality": self.quality,
            "on_brand": self.on_brand,
            "conscientious": self.conscientious,
            "relevant": self.relevant,
            "reason": self.reason,
        }


REJECTED_PARSE = JudgeVerdict(
    index=-1, approve=False, quality=0, on_brand=False,
    conscientious=False, relevant=False, reason="unparseable judge output (refuse by default)",
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _coerce_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
    return None


def parse_judge_response(raw: str, expected_count: int, kind: str = "post") -> list[JudgeVerdict]:
    """Parse the judge's JSON, refusing by default on anything malformed.

    Returns exactly ``expected_count`` verdicts, indexed 0..n-1. Any candidate
    whose verdict is missing, malformed, or ambiguous gets a REJECT verdict.

    ``kind`` matters for the 'relevant' gate: replies MUST be explicitly
    relevant; standalone posts only fail relevance if the judge explicitly
    says False (the field is documented as replies-only).
    """
    rejects = [
        JudgeVerdict(i, False, 0, False, False, False,
                     "no verdict returned (refuse by default)")
        for i in range(expected_count)
    ]
    if not raw or not raw.strip():
        return rejects

    text = raw.strip()
    # Tolerate markdown fences around the JSON.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    m = _JSON_BLOCK.search(text)
    if not m:
        return rejects
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return rejects

    raw_verdicts = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(raw_verdicts, list):
        return rejects

    out = list(rejects)
    for item in raw_verdicts:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < expected_count):
            continue
        approve = _coerce_bool(item.get("approve"))
        on_brand = _coerce_bool(item.get("on_brand"))
        conscientious = _coerce_bool(item.get("conscientious"))
        relevant = _coerce_bool(item.get("relevant"))
        try:
            quality = int(item.get("quality", 0))
        except (TypeError, ValueError):
            quality = 0
        reason = str(item.get("reason", ""))[:500]

        # Refuse-by-default: every gate must be an explicit, affirmative True
        # AND quality must clear the floor. Missing/None booleans = reject.
        # 'relevant' is a replies-only dimension: for standalone posts the
        # judge has no target to be relevant TO, so the field is ignored
        # (models fill it inconsistently with false/None for posts).
        relevant_ok = (relevant is True) if kind == "reply" else True
        final_approve = (
            approve is True
            and on_brand is True
            and conscientious is True
            and relevant_ok
            and APPROVE_MIN_QUALITY <= quality <= 10
        )
        if not final_approve and approve is True and not reason:
            reason = "approved by judge but failed strict gate (refuse by default)"
        out[idx] = JudgeVerdict(
            index=idx,
            approve=final_approve,
            quality=max(0, min(10, quality)),
            on_brand=bool(on_brand),
            conscientious=bool(conscientious),
            relevant=bool(relevant),
            reason=reason or ("approved" if final_approve else "rejected"),
        )
    return out
