# app/services/vision_judge.py
"""Shared pixel-level VISION-JUDGE core (owner finding #3, 2026-07-02).

This is the single source of truth for "look at the rendered pixels and score
them" — the prompt, the tolerant JSON parser, the pass rule, and the LiteLLM
multimodal call. It lives in ``app/`` (not ``scripts/``) because the LIVE
result-image quality gate in ``app.services.image_pipeline`` imports it and
the production Docker image ships ONLY ``app/`` (see backend/Dockerfile's
explicit COPY allowlist). The offline eval harness
(``scripts/eval_image_quality.py``) reuses these exact primitives so online
and offline judging share one prompt, one parser and one pass rule — a score
produced by the live gate is directly comparable to the eval harness's.

Design notes (carried over from the eval harness where this code originated):
- The judge call is a DIRECT LiteLLM ``acompletion`` (chat-completions)
  multimodal request — the well-trodden vision path both OpenAI (gpt-4o) and
  Gemini (gemini/gemini-flash-latest) accept. We do NOT route through
  ``llm_service``: that wrapper targets the Responses API and structured TEXT
  output; a direct multimodal chat call is simpler and easy to fake in tests.
- The image is delivered as an ``image_url`` content part holding a base64
  ``data:`` URL (fetched bytes) so the model sees the real pixels — not a URL
  it would have to (and could not) browse.
- ``max_tokens`` MUST leave room for REASONING tokens: "thinking" models
  (gemini-2.5-flash / gemini-flash-latest) count internal reasoning against
  max_tokens, so 400 truncated the visible JSON to "{" and every verdict came
  back unparseable (observed 2026-07-02: 95/100 unparseable at 400). 2000
  leaves ~1500 for thinking + ~200 for the JSON body.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Pass rule constants (shared by the live gate and the offline harness)
# ---------------------------------------------------------------------------

PASS_FIDELITY = 7
PASS_RELEVANCE = 7

KNOWN_BLOCKERS = (
    "deformed_face",
    "off_topic",
    "placeholder_or_blank",
    "text_garbage",
    "ip_violation",
)


@dataclass(frozen=True)
class VisionScore:
    """Structured score returned by the vision judge."""

    fidelity: int
    relevance: int
    style_ok: bool
    blocking_reasons: list[str]
    notes: str = ""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

VISION_SYSTEM = (
    "You are an exacting art director QA-ing AI-generated portrait/scene images "
    "for a personality-quiz app. You are shown the ACTUAL rendered image. Judge "
    "the PIXELS, not a description. Be calibrated; do not pass deformed, blurry, "
    "blank, or off-topic images."
)


def build_vision_user_prompt(
    *, subject: str, topic: str, expected_description: str | None
) -> str:
    exp = (
        f"\nExpected (ground truth, if any): {expected_description}"
        if expected_description
        else ""
    )
    blockers = ", ".join(KNOWN_BLOCKERS)
    return (
        "Evaluate the attached image.\n"
        f"Subject it should depict: {subject}\n"
        f"Topic / universe: {topic or '(unspecified)'}{exp}\n\n"
        "Score on these axes and return ONLY a JSON object:\n"
        '  "fidelity": int 1-10  '
        "(clean, well-rendered image — NOT deformed/blurry/artifacted; 10=flawless)\n"
        '  "relevance": int 1-10  '
        "(does the image actually match the subject & topic? 10=spot on)\n"
        '  "style_ok": bool  '
        "(on-brand: a single coherent illustrated portrait, consistent palette)\n"
        '  "blocking_reasons": string[]  '
        f"(zero or more of: {blockers}; [] if none)\n"
        '  "notes": string  (<= 30 words; what you saw)\n\n'
        "Do NOT flag branded/trademarked characters as IP unless the image is a "
        "verbatim copyrighted logo/frame; we intentionally pass branded "
        "characters. Reserve 'ip_violation' for blatant cases only.\n"
        "Return ONLY the JSON object, no prose."
    )


# ---------------------------------------------------------------------------
# LiteLLM multimodal client (real; fakeable in tests via the same interface)
# ---------------------------------------------------------------------------


class LiteLLMVisionClient:
    """Real vision judge via a DIRECT LiteLLM ``acompletion`` multimodal call.

    Constructed only on a real run. The image is sent as an ``image_url``
    content part holding a base64 ``data:`` URL, which is the multimodal format
    both OpenAI (gpt-4o) and Gemini accept through LiteLLM.
    """

    def __init__(self) -> None:
        import litellm  # local import keeps the fake/test path dependency-light

        litellm.suppress_debug_info = True
        litellm.drop_params = True
        self._litellm = litellm

    async def score(
        self,
        *,
        model: str,
        subject: str,
        topic: str,
        expected_description: str | None,
        image_data_url: str,
        timeout_s: int,
    ) -> VisionScore:  # pragma: no cover - network path
        user_text = build_vision_user_prompt(
            subject=subject, topic=topic, expected_description=expected_description
        )
        messages = [
            {"role": "system", "content": VISION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
        resp = await self._litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=2000,
            timeout=timeout_s,
            response_format={"type": "json_object"},
        )
        text = extract_response_text(resp)
        return parse_vision_score(text)


def extract_response_text(resp: Any) -> str:
    """Pull the assistant text out of a LiteLLM chat-completions response."""
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        msg = (
            first.get("message")
            if isinstance(first, dict)
            else getattr(first, "message", None)
        )
        content = (
            msg.get("content")
            if isinstance(msg, dict)
            else getattr(msg, "content", None)
        )
        if isinstance(content, str):
            return content
    return ""


# ---------------------------------------------------------------------------
# Parsing + verdict
# ---------------------------------------------------------------------------


def parse_vision_score(text: str) -> VisionScore:
    """Parse a (possibly fenced) JSON judge response into a VisionScore.

    Tolerant: clamps scores to 1-10, coerces booleans, keeps only known
    blockers, and degrades a garbage response to a clearly-failing score (so a
    malformed judge reply never silently passes).
    """
    raw = (text or "").strip()
    # Strip ```json fences if present.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rstrip("`").rstrip()
    # Grab the first {...} block.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    obj: dict[str, Any] = {}
    if m:
        try:
            obj = json.loads(m.group(0))
        except Exception:
            obj = {}

    def _int(key: str) -> int:
        try:
            return max(1, min(10, int(round(float(obj.get(key, 0))))))
        except Exception:
            return 1

    blockers_raw = obj.get("blocking_reasons") or []
    if not isinstance(blockers_raw, list):
        blockers_raw = [str(blockers_raw)]
    blockers = [str(b).strip() for b in blockers_raw if str(b).strip()]

    if not obj:
        # Unparseable judge output -> explicit failing score, not a pass.
        return VisionScore(
            fidelity=1,
            relevance=1,
            style_ok=False,
            blocking_reasons=["unparseable_judge_output"],
            notes=raw[:200],
        )

    return VisionScore(
        fidelity=_int("fidelity"),
        relevance=_int("relevance"),
        style_ok=bool(obj.get("style_ok", False)),
        blocking_reasons=blockers,
        notes=str(obj.get("notes", ""))[:300],
    )


def verdict_from_score(score: VisionScore) -> str:
    """Apply the harness pass rule: fidelity>=7 AND relevance>=7 AND style_ok
    AND no blocking_reasons."""
    passed = (
        score.fidelity >= PASS_FIDELITY
        and score.relevance >= PASS_RELEVANCE
        and score.style_ok
        and not score.blocking_reasons
    )
    return "pass" if passed else "fail"


# ---------------------------------------------------------------------------
# Image resolution (URL / local path / data URL) -> base64 data URL
# ---------------------------------------------------------------------------

DATA_URL_RE = re.compile(r"^data:image/[\w.+-]+;base64,", re.IGNORECASE)


def ext_to_mime(suffix: str) -> str:
    s = suffix.lower().lstrip(".")
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(s, "image/jpeg")


def read_local_image(path: str) -> str | None:
    try:
        p = Path(path)
        data = p.read_bytes()
        if not data:
            return None
        mime = ext_to_mime(p.suffix)
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


async def to_data_url(
    *,
    image_url: str | None = None,
    image_path: str | None = None,
    timeout_s: int,
    http_client: Any | None = None,
) -> str | None:
    """Resolve an image to a base64 ``data:`` URL, or None if unavailable.

    Handles: already-a-data-URL, local ``image_path``, and ``image_url``
    (http(s) fetched via httpx, OR a ``data:`` URL, OR a local path supplied as
    a URL). A network/HTTP/decoding failure returns None.
    """
    # 1) Explicit local path.
    if image_path:
        return read_local_image(image_path)

    url = (image_url or "").strip()
    if not url:
        return None

    # 2) Already a data URL.
    if DATA_URL_RE.match(url):
        return url

    # 3) A local path handed in via the URL field.
    if not url.lower().startswith(("http://", "https://")):
        return read_local_image(url)

    # 4) Remote fetch via httpx.
    try:
        if http_client is not None:
            resp = await http_client.get(url, timeout=timeout_s)
        else:  # pragma: no cover - exercised only on real runs
            import httpx

            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(url, timeout=timeout_s)
        status = getattr(resp, "status_code", 0)
        if status != 200:
            return None
        content = resp.content
        if not content:
            return None
        ctype = ""
        headers = getattr(resp, "headers", {}) or {}
        try:
            ctype = headers.get("content-type", "") or headers.get("Content-Type", "")
        except Exception:
            ctype = ""
        mime = ctype.split(";")[0].strip() if ctype.startswith("image/") else "image/jpeg"
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


__all__ = [
    "DATA_URL_RE",
    "KNOWN_BLOCKERS",
    "PASS_FIDELITY",
    "PASS_RELEVANCE",
    "LiteLLMVisionClient",
    "VISION_SYSTEM",
    "VisionScore",
    "build_vision_user_prompt",
    "ext_to_mime",
    "extract_response_text",
    "parse_vision_score",
    "read_local_image",
    "to_data_url",
    "verdict_from_score",
]
