# backend/app/agent/schemas.py
"""
Agent ↔ LLM Schemas (strict, centralized)

Design goals
------------
- Single source of truth for tool I/O and agent state shapes.
- Strict Pydantic models (extra='forbid', trimmed strings).
- JSON Schema builders that *precisely* match the Pydantic models.
- Consistent OpenAI Responses API envelopes: {"name", "strict": True, "schema": {...}}
- Minimal surprises: no mixing list schemas with object models (and vice versa).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Type
from typing import Optional as Opt
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field

# ---------------------------------------------------------------------------
# Optional app configuration & canonicals
# ---------------------------------------------------------------------------

try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    settings = None  # builders handle None safely

try:
    from app.agent.canonical_sets import canonical_for  # type: ignore
except Exception:  # pragma: no cover
    def canonical_for(category: Opt[str]) -> Opt[List[str]]:  # type: ignore
        return None


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------

class StrictBase(BaseModel):
    """All models forbid extra fields and trim whitespace."""
    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }


# ---------------------------------------------------------------------------
# Core content models (used in agent state & some tools)
# ---------------------------------------------------------------------------

class Synopsis(StrictBase):
    """High-level summary of the quiz category."""
    title: str = Field(..., min_length=1)
    summary: str = Field(default="", min_length=0)


class CharacterProfile(StrictBase):
    """One playable/guessable character."""
    name: str = Field(..., min_length=1)
    short_description: str = Field(
        default="",
        min_length=0,
        validation_alias=AliasChoices("short_description", "shortDescription"),
    )
    profile_text: str = Field(
        default="",
        min_length=0,
        validation_alias=AliasChoices("profile_text", "profileText"),
    )
    image_url: Opt[str] = Field(
        default=None,
        validation_alias=AliasChoices("image_url", "imageUrl", "image"),
    )


class QuizQuestion(StrictBase):
    """
    State/APIs use a simple question shape with "options" as list of dicts:
    {"text": str, "image_url"?: str}. Tools return the richer QuestionOut.
    """
    question_text: str = Field(..., min_length=1)
    options: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Structured-output variants (preferred for LLM responses)
# ---------------------------------------------------------------------------

class QuestionOption(StrictBase):
    text: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("text", "label", "option"),
    )
    image_url: Opt[str] = Field(
        default=None,
        validation_alias=AliasChoices("image_url", "imageUrl", "image"),
    )


class QuestionOut(StrictBase):
    """Structured tool output; graph converts this to QuizQuestion for state."""
    question_text: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("question_text", "question", "text"),
    )
    options: List[QuestionOption]


class QuestionList(StrictBase):
    questions: List[QuestionOut]


class CharacterArchetypeList(StrictBase):
    """For tools that return *names* of archetypes (not full profiles)."""
    archetypes: List[str] = Field(default_factory=list)


class CharacterSelection(StrictBase):
    """For tools that must pick winners from a candidate list (simple picker)."""
    selected_names: List[str]


class SafetyCheck(StrictBase):
    allowed: bool
    categories: Opt[List[str]] = None
    warnings: Opt[List[str]] = None
    rationale: Opt[str] = None


class ErrorAnalysis(StrictBase):
    retryable: bool
    reason: str
    details: Opt[Dict[str, str]] = None


class FailureExplanation(StrictBase):
    message: str
    tips: Opt[List[str]] = None


class ImagePrompt(StrictBase):
    prompt: str
    negative_prompt: Opt[str] = None


class ReuseItem(StrictBase):
    ideal_name: str = Field(..., min_length=1, description="Exact ideal archetype name.")
    existing_name: str = Field(..., min_length=1, description="Matched existing character name.")
    reason: Opt[str] = Field(default=None, description="Short rationale for reuse choice.")

class ImproveItem(StrictBase):
    ideal_name: str = Field(..., min_length=1)
    existing_name: str = Field(..., min_length=1)
    feedback: Opt[str] = Field(default=None, description="What to improve (tone, coverage, style).")


# ---------------------------------------------------------------------------
# Typed history entry & decider output (adaptive flow)
# ---------------------------------------------------------------------------

class QuestionAnswer(StrictBase):
    question_index: int = Field(default=0, ge=0)
    question_text: str
    answer_text: str
    option_index: Opt[int] = Field(
        default=None,
        validation_alias=AliasChoices("option_index", "optionIndex"),
    )


class NextStepDecision(StrictBase):
    action: Literal["ASK_ONE_MORE_QUESTION", "FINISH_NOW"]
    winning_character_name: Opt[str] = Field(
        default=None,
        validation_alias=AliasChoices("winning_character_name", "winningCharacterName", "winner"),
    )
    confidence: Opt[float] = None  # 0.0–1.0 (graph may normalize 0–100 to 0–1)


# ---------------------------------------------------------------------------
# Planning tool models
# ---------------------------------------------------------------------------

class InitialPlan(StrictBase):
    """Output of the initial planning stage."""
    title: Opt[str] = None
    synopsis: str = ""
    ideal_archetypes: List[str] = Field(default_factory=list)
    ideal_count_hint: Opt[int] = Field(
        default=None,
        description="Planner’s suggested number of outcomes (config/canonical-driven)."
    )


class CharacterCastingDecision(StrictBase):
    reuse: List[ReuseItem] = Field(default_factory=list)
    improve: List[ImproveItem] = Field(default_factory=list)
    create: List[str] = Field(default_factory=list, description="Ideal names to create from scratch.")


class NormalizedTopic(StrictBase):
    """
    Output of normalize_topic.
    Field 'category' is the normalized, quiz-ready category string.
    """
    category: str = Field(description="Normalized quiz category (e.g., 'Gilmore Girls Characters').")
    outcome_kind: Literal["characters", "types", "archetypes", "profiles"] = Field(
        description="What kind of outcomes the quiz should produce."
    )
    creativity_mode: Literal["whimsical", "balanced", "factual"] = Field(
        description="How creative/grounded the content should be."
    )
    rationale: str = Field(description="Brief explanation of the normalization decision.")
    intent: Opt[str] = Field(
        default=None,
        description="Broader intent classification (e.g., identify, sorting, alignment, compatibility)."
    )


# ---------------------------------------------------------------------------
# Canonical agent state model (transport/storage)
# ---------------------------------------------------------------------------

class AgentGraphStateModel(StrictBase):
    """
    Canonical cache/transport model for agent state. Mirrors the GraphState keys.
    """
    # Identifiers / session
    session_id: UUID
    trace_id: str
    category: str

    # Conversation history (stored as plain dicts for Redis)
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    # Error flags
    is_error: bool = False
    error_message: Opt[str] = None
    error_count: int = 0

    # Steering/context
    rag_context: Opt[List[Dict[str, Any]]] = None
    outcome_kind: Opt[str] = None
    creativity_mode: Opt[str] = None
    topic_analysis: Opt[Dict[str, Any]] = None

    # Content
    synopsis: Opt[Synopsis] = None
    ideal_archetypes: List[str] = Field(default_factory=list)
    generated_characters: List[CharacterProfile] = Field(default_factory=list)
    generated_questions: List[QuizQuestion] = Field(default_factory=list)

    # Progress / gating
    agent_plan: Opt[Dict[str, Any]] = None
    quiz_history: List[QuestionAnswer] = Field(default_factory=list)
    baseline_count: int = 0
    baseline_ready: bool = False
    ready_for_questions: bool = False
    should_finalize: Opt[bool] = None
    current_confidence: Opt[float] = None

    # Final result (kept loose)
    final_result: Opt[Dict[str, Any]] = None
    last_served_index: Opt[int] = None


# ---------------------------------------------------------------------------
# Internal helpers for dynamic JSON Schema parameters
# ---------------------------------------------------------------------------

def _quiz_setting(name: str, default: Any) -> Any:
    """Safe getter for settings.quiz.<name> with default fallback."""
    try:
        return getattr(getattr(settings, "quiz", object()), name, default)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        return default


def _nullable(t: Dict[str, Any]) -> Dict[str, Any]:
    """Allow null in addition to a concrete type."""
    return {"anyOf": [t, {"type": "null"}]}


def _wrap(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI Responses API envelope (pre-envelope; llm_service wraps it)."""
    return {"name": name, "strict": True, "schema": schema}


# ---------------------------------------------------------------------------
# JSON Schema builders (match Pydantic models exactly)
# ---------------------------------------------------------------------------

def build_synopsis_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "Synopsis",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "summary": {"type": "string"},
        },
        "required": ["title", "summary"],
    }
    return _wrap("Synopsis", schema)


def build_character_profile_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "CharacterProfile",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "short_description": {"type": "string"},
            "profile_text": {"type": "string"},
            "image_url": _nullable({"type": "string"}),
        },
        "required": ["name", "short_description", "profile_text"],
    }
    return _wrap("CharacterProfile", schema)


def build_character_profile_list_jsonschema() -> Dict[str, Any]:
    item_schema = build_character_profile_jsonschema()["schema"]
    schema = {
        "title": "CharacterProfileList",
        "type": "array",
        "items": item_schema,
        "minItems": 1,
    }
    return _wrap("CharacterProfileList", schema)


def build_question_option_jsonschema() -> Dict[str, Any]:
    # Sub-schema (no envelope) used inside QuestionOut
    return {
        "title": "QuestionOption",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string", "minLength": 1},
            "image_url": _nullable({"type": "string"}),
        },
        "required": ["text"],
    }


def build_question_out_jsonschema(*, max_options: Opt[int] = None) -> Dict[str, Any]:
    default_cap = max(2, int(_quiz_setting("max_options_m", 4)))
    cap = int(max_options) if isinstance(max_options, int) and max_options > 0 else default_cap
    schema = {
        "title": "QuestionOut",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question_text": {"type": "string", "minLength": 1},
            "options": {
                "type": "array",
                "items": build_question_option_jsonschema(),
                "minItems": 2,
                "maxItems": cap,
            },
        },
        "required": ["question_text", "options"],
    }
    return _wrap("QuestionOut", schema)


def build_question_list_jsonschema(*, count: Opt[int] = None, max_options: Opt[int] = None) -> Dict[str, Any]:
    default_n = max(1, int(_quiz_setting("baseline_questions_n", 5)))
    n = int(count) if isinstance(count, int) and count > 0 else default_n
    qo_schema = build_question_out_jsonschema(max_options=max_options)["schema"]
    schema = {
        "title": "QuestionList",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "questions": {
                "type": "array",
                "items": qo_schema,
                "minItems": n,
                "maxItems": max(n, default_n) + 5,
            }
        },
        "required": ["questions"],
    }
    return _wrap("QuestionList", schema)


def build_character_archetype_list_jsonschema(category: Opt[str] = None) -> Dict[str, Any]:
    default_min = int(_quiz_setting("min_characters", 2))
    default_max = int(_quiz_setting("max_characters", 32))
    canon = canonical_for(category) if category else None
    canon_len = len(canon) if canon else None
    dyn_max = max(default_max, canon_len) if canon_len else default_max

    schema = {
        "title": "CharacterArchetypeList",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "archetypes": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": default_min,
                "maxItems": dyn_max,
            }
        },
        "required": ["archetypes"],
    }
    return _wrap("CharacterArchetypeList", schema)


def build_character_casting_decision_jsonschema() -> Dict[str, Any]:
    reuse_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ideal_name": {"type": "string", "minLength": 1},
            "existing_name": {"type": "string", "minLength": 1},
            "reason": _nullable({"type": "string"}),
        },
        "required": ["ideal_name", "existing_name"],
    }
    improve_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ideal_name": {"type": "string", "minLength": 1},
            "existing_name": {"type": "string", "minLength": 1},
            "feedback": _nullable({"type": "string"}),
        },
        "required": ["ideal_name", "existing_name"],
    }
    schema = {
        "title": "CharacterCastingDecision",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reuse": {"type": "array", "items": reuse_schema},
            "improve": {"type": "array", "items": improve_schema},
            "create": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
        "required": ["reuse", "improve", "create"],
    }
    return _wrap("CharacterCastingDecision", schema)


def build_initial_plan_jsonschema(category: Opt[str] = None) -> Dict[str, Any]:
    default_min = 2
    default_max = 32

    min_n = int(_quiz_setting("min_characters", default_min))
    max_n = int(_quiz_setting("max_characters", default_max))

    canon = canonical_for(category) if category else None
    canon_len = len(canon) if canon else None
    dynamic_max = max(max_n, canon_len) if canon_len else max_n

    schema = {
        "title": "InitialPlan",
        "description": "Output of the initial planning stage.",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "description": "Catchy quiz title."},
            "synopsis": {"type": "string", "description": "Engaging synopsis (3–4 sentences)."},
            "ideal_archetypes": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": min_n,
                "maxItems": dynamic_max,
                "description": "Archetype names (count is config/canonical-driven).",
            },
            "ideal_count_hint": _nullable({"type": "integer"}),
        },
        "required": ["synopsis", "ideal_archetypes"],
    }
    return _wrap("InitialPlan", schema)


def build_normalized_topic_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "NormalizedTopic",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category": {"type": "string", "minLength": 1},
            "outcome_kind": {"type": "string", "enum": ["characters", "types", "archetypes", "profiles"]},
            "creativity_mode": {"type": "string", "enum": ["whimsical", "balanced", "factual"]},
            "rationale": {"type": "string"},
            "intent": _nullable({"type": "string"}),
        },
        "required": ["category", "outcome_kind", "creativity_mode", "rationale"],
    }
    return _wrap("NormalizedTopic", schema)


def build_next_step_decision_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "NextStepDecision",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["ASK_ONE_MORE_QUESTION", "FINISH_NOW"]},
            "winning_character_name": _nullable({"type": "string"}),
            "confidence": _nullable({"type": "number", "minimum": 0.0, "maximum": 1.0}),
        },
        "required": ["action"],
    }
    return _wrap("NextStepDecision", schema)


def build_safety_check_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "SafetyCheck",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "allowed": {"type": "boolean"},
            "categories": _nullable({"type": "array", "items": {"type": "string"}}),
            "warnings": _nullable({"type": "array", "items": {"type": "string"}}),
            "rationale": _nullable({"type": "string"}),
        },
        "required": ["allowed"],
    }
    return _wrap("SafetyCheck", schema)


def build_error_analysis_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "ErrorAnalysis",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "retryable": {"type": "boolean"},
            "reason": {"type": "string"},
            "details": _nullable({"type": "object", "additionalProperties": {"type": "string"}}),
        },
        "required": ["retryable", "reason"],
    }
    return _wrap("ErrorAnalysis", schema)


def build_failure_explanation_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "FailureExplanation",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message": {"type": "string"},
            "tips": _nullable({"type": "array", "items": {"type": "string"}}),
        },
        "required": ["message"],
    }
    return _wrap("FailureExplanation", schema)


def build_image_prompt_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "ImagePrompt",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "negative_prompt": _nullable({"type": "string"}),
        },
        "required": ["prompt"],
    }
    return _wrap("ImagePrompt", schema)

def build_final_result_jsonschema() -> Dict[str, Any]:
    schema = {
        "title": "FinalResult",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "image_url": _nullable({"type": "string"}),
        },
        "required": ["title", "description"],
    }
    return _wrap("FinalResult", schema)

# ---------------------------------------------------------------------------
# Prebuilt, config-driven defaults
# ---------------------------------------------------------------------------

INITIAL_PLAN_JSONSCHEMA: Dict[str, Any] = build_initial_plan_jsonschema()
SYNOPSIS_JSONSCHEMA: Dict[str, Any] = build_synopsis_jsonschema()
CHARACTER_PROFILE_JSONSCHEMA: Dict[str, Any] = build_character_profile_jsonschema()
QUESTION_OUT_JSONSCHEMA: Dict[str, Any] = build_question_out_jsonschema()
QUESTION_LIST_JSONSCHEMA: Dict[str, Any] = build_question_list_jsonschema()
CHARACTER_ARCHETYPE_LIST_JSONSCHEMA: Dict[str, Any] = build_character_archetype_list_jsonschema()
CHARACTER_CASTING_DECISION_JSONSCHEMA: Dict[str, Any] = build_character_casting_decision_jsonschema()
NORMALIZED_TOPIC_JSONSCHEMA: Dict[str, Any] = build_normalized_topic_jsonschema()
NEXT_STEP_DECISION_JSONSCHEMA: Dict[str, Any] = build_next_step_decision_jsonschema()
SAFETY_CHECK_JSONSCHEMA: Dict[str, Any] = build_safety_check_jsonschema()
ERROR_ANALYSIS_JSONSCHEMA: Dict[str, Any] = build_error_analysis_jsonschema()
FAILURE_EXPLANATION_JSONSCHEMA: Dict[str, Any] = build_failure_explanation_jsonschema()
IMAGE_PROMPT_JSONSCHEMA: Dict[str, Any] = build_image_prompt_jsonschema()


# ---------------------------------------------------------------------------
# Registry: map tool_name → expected response *model*
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: Dict[str, Type[StrictBase]] = {
    "initial_planner": InitialPlan,
    "topic_normalizer": NormalizedTopic,
    "character_list_generator": CharacterArchetypeList,
    "character_selector": CharacterCastingDecision,

    "profile_writer": CharacterProfile,
    "profile_improver": CharacterProfile,

    "synopsis_generator": Synopsis,

    "question_generator": QuestionList,
    "next_question_generator": QuestionOut,

    "safety_checker": SafetyCheck,
    "error_analyzer": ErrorAnalysis,
    "failure_explainer": FailureExplanation,
    "image_prompt_enhancer": ImagePrompt,

    "decision_maker": NextStepDecision,
}


def schema_for(tool_name: str) -> Opt[Type[StrictBase]]:
    """Look up the default response model for a tool (BaseModel shapes only)."""
    return SCHEMA_REGISTRY.get(tool_name)


# ---------------------------------------------------------------------------
# Registry of JSON Schema builders (OpenAI Responses API envelopes)
# ---------------------------------------------------------------------------

JSONSCHEMA_REGISTRY: Dict[str, Any] = {
    "initial_planner": build_initial_plan_jsonschema,
    "topic_normalizer": build_normalized_topic_jsonschema,
    "character_list_generator": build_character_archetype_list_jsonschema,
    "character_selector": build_character_casting_decision_jsonschema,

    "profile_writer": build_character_profile_jsonschema,
    "profile_improver": build_character_profile_jsonschema,
    "profile_batch_writer": build_character_profile_list_jsonschema,  # List[CharacterProfile]

    "synopsis_generator": build_synopsis_jsonschema,

    "question_generator": build_question_list_jsonschema,   # object with "questions"
    "next_question_generator": build_question_out_jsonschema,

    "safety_checker": build_safety_check_jsonschema,
    "error_analyzer": build_error_analysis_jsonschema,
    "failure_explainer": build_failure_explanation_jsonschema,
    "image_prompt_enhancer": build_image_prompt_jsonschema,

    "decision_maker": build_next_step_decision_jsonschema,
    "final_profile_writer": build_final_result_jsonschema,
}


def jsonschema_for(tool_name: str, **kwargs) -> Opt[Dict[str, Any]]:
    """
    Build and return the strict JSON Schema envelope for the given tool.
    Accepts kwargs to parameterize dynamic limits (e.g., category, count, max_options).
    """
    builder = JSONSCHEMA_REGISTRY.get(tool_name)
    if not builder:
        return None
    try:
        return builder(**kwargs) if kwargs else builder()
    except Exception:
        # Defensive: allow caller to fall back to Pydantic validation
        return None
