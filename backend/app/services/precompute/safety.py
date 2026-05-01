"""§21 Phase 3 — topic-policy & prompt-injection guards.

This module collects the small, security-critical helpers that gate the
build pipeline:

- `assert_topic_can_be_enqueued(topic)` — reject `banned` topics
  (`AC-PRECOMP-SAFETY-1`).
- `evaluator_constraints_for(topic)` — escalate `restricted` topics to
  Tier-3 with `τ_pass=9` (`AC-PRECOMP-SAFETY-2`).
- `wrap_user_input(text)` — produce a delimited block that is safe to
  splice into a USER message (`AC-PRECOMP-SAFETY-3` + `AC-PRECOMP-SEC-2`).
- `wrap_retrieved_block(text)` — same idea for web-retrieval snippets so
  the model is instructed to treat them as data, not control flow
  (`AC-PRECOMP-SEC-2`).
- `record_vision_rejection(...)` — uniform shape for vision-evaluator
  rejections recorded for offline review (`AC-PRECOMP-SAFETY-4`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class PolicyStatus(str, Enum):
    """Mirrors `topics.policy_status` enum.

    Kept as a thin string-Enum so the values round-trip transparently
    through SQLAlchemy / JSON without bespoke serializers.
    """

    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    BANNED = "banned"


class TopicBannedError(Exception):
    """`AC-PRECOMP-SAFETY-1` — raised by `assert_topic_can_be_enqueued`.

    Carries a stable `code` so the worker can persist a deterministic
    ledger error for audit without leaking the message to clients.
    """

    code: str = "TOPIC_BANNED"

    def __init__(self, topic_id: str | None, *, slug: str | None = None) -> None:
        super().__init__(f"Topic banned by policy: id={topic_id!s} slug={slug!s}")
        self.topic_id = topic_id
        self.slug = slug


@dataclass(frozen=True)
class EvaluatorConstraints:
    """Per-topic evaluator overrides applied by the build worker.

    `force_tier` selects the minimum tier the worker MUST start at;
    `pass_score` overrides the global `τ_pass` for THIS topic only.
    """

    force_tier: str | None  # "cheap" | "strong" | "strong+search" | None
    pass_score: int | None
    require_two_judge: bool = False


def assert_topic_can_be_enqueued(*, policy_status: str | None, topic_id: str | None = None,
                                  slug: str | None = None) -> None:
    """Reject `banned` topics; allow `allowed` / `restricted` to proceed.

    Treats unknown values as `allowed` — the schema CHECK constraint already
    rejects out-of-band values at write time, so this code only runs against
    persisted topics whose status is one of the three known values.
    """
    if (policy_status or "").lower() == PolicyStatus.BANNED.value:
        raise TopicBannedError(topic_id, slug=slug)


def evaluator_constraints_for(
    *,
    policy_status: str | None,
    default_pass_score: int,
    restricted_pass_score: int = 9,
) -> EvaluatorConstraints:
    """Compute per-topic evaluator overrides.

    `restricted` → force `strong+search` and bump `τ_pass`
    (`AC-PRECOMP-SAFETY-2`); `allowed` → no overrides.
    """
    status = (policy_status or PolicyStatus.ALLOWED.value).lower()
    if status == PolicyStatus.RESTRICTED.value:
        return EvaluatorConstraints(
            force_tier="strong+search",
            pass_score=int(restricted_pass_score),
            require_two_judge=True,
        )
    return EvaluatorConstraints(
        force_tier=None,
        pass_score=int(default_pass_score),
        require_two_judge=False,
    )


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
    """`AC-PRECOMP-SAFETY-3` — wrap user-supplied category text in a
    delimited block. Callers MUST splice this output ONLY into a USER
    role message; a system-prompt template that includes user input is
    a violation of the AC even with this helper.
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


# ---------------------------------------------------------------------------
# Vision rejection record (`AC-PRECOMP-SAFETY-4`)
# ---------------------------------------------------------------------------


class VisionRejectionReason(str, Enum):
    NSFW = "NSFW"
    MINOR = "MINOR"
    LOGO = "LOGO"
    OFF_TOPIC = "OFF_TOPIC"


@dataclass(frozen=True)
class VisionRejectionRecord:
    asset_id: str
    reason: VisionRejectionReason
    detail: str = ""


def record_vision_rejection(
    *, asset_id: str, reason: VisionRejectionReason | str, detail: str = ""
) -> VisionRejectionRecord:
    """Build a deterministic record for offline review.

    The persistence layer (Phase 6) will write these into `content_flags`
    with `reason_code` set to the reason; for now Phase 3 just standardises
    the shape so call sites do not invent ad-hoc dicts.
    """
    if isinstance(reason, str):
        reason = VisionRejectionReason(reason)
    return VisionRejectionRecord(asset_id=asset_id, reason=reason, detail=detail or "")
