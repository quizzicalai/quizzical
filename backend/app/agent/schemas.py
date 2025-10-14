# backend/app/agent/schemas.py
"""
Agent ↔ LLM Schemas (strict, centralized)

V0 principles:
- Strict models (extra='forbid'); no tolerant aliases.
- Single source of truth for tool I/O and agent state shapes.
- State questions use the simple "state shape": List[{"text", "image_url"?}] per option.
- Tools may return richer shapes (e.g., QuestionOut) which we convert to state shape.
- Registry mapping tool_name → Pydantic model for callers that don't pass response_model.

Additions (schema builders):
- JSON Schema builders for all structured outputs we request from the model.
- Builders are dynamic where useful (e.g., option counts, question counts, canonical set sizes).
- Each builder returns an object compatible with OpenAI Responses API structured outputs:
  {"name": "<ModelName>", "strict": true, "schema": {...}}.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional as Opt, Type, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Config/canonical imports for dynamic schema building
try:
    # Local app settings (provides quiz.min_characters / quiz.max_characters / etc.)
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    settings = None  # Safe fallback; builders below handle None

try:
    from app.agent.canonical_sets import canonical_for
except Exception:  # pragma: no cover
    def canonical_for(category):  # type: ignore
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
    short_description: str = Field(default="", min_length=0)
    profile_text: str = Field(default="", min_length=0)
    image_url: Opt[str] = None


class QuizQuestion(StrictBase):
    """
    Question shape used by the agent state / API.
    State shape keeps options as a list of dicts: {"text": str, "image_url"?: str}
    """
    question_text: str = Field(..., min_length=1)
    options: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Structured-output variants (preferred for LLM responses)
# ---------------------------------------------------------------------------

class QuestionOption(StrictBase):
    text: str = Field(..., min_length=1)
    image_url: Opt[str] = None


class QuestionOut(StrictBase):
    """Structured tool output; graph converts this to QuizQuestion for state."""
    question_text: str = Field(..., min_length=1)
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


# ---------------------------------------------------------------------------
# Typed history entry & decider output (adaptive flow)
# ---------------------------------------------------------------------------

class QuestionAnswer(StrictBase):
    question_index: int = Field(default=0, ge=0)
    question_text: str
    answer_text: str
    option_index: Opt[int] = None


class NextStepDecision(StrictBase):
    action: Literal["ASK_ONE_MORE_QUESTION", "FINISH_NOW"]
    winning_character_name: Opt[str] = None
    confidence: Opt[float] = None  # 0.0–1.0 (tools may return 0–100; graph normalizes)


# ---------------------------------------------------------------------------
# Planning tool models (centralized here for consistency)
# ---------------------------------------------------------------------------

class InitialPlan(StrictBase):
    """Output of the initial planning stage."""
    # Title can be omitted by providers; default it so the model validates.
    # Optional in Pydantic v2 still needs an explicit default to avoid "Field required".
    title: Opt[str] = None
    synopsis: str = ""
    ideal_archetypes: List[str] = Field(default_factory=list)
    ideal_count_hint: Opt[int] = Field(
        default=None,
        description="Planner’s suggested number of outcomes (config/canonical-driven)."
    )


class CharacterCastingDecision(StrictBase):
    """Decisions whether to reuse, improve, or create characters."""
    reuse: List[Dict] = Field(default_factory=list, description="Existing characters to reuse as-is.")
    improve: List[Dict] = Field(default_factory=list, description="Existing characters to improve.")
    create: List[str] = Field(default_factory=list, description="New archetypes to create from scratch.")


class NormalizedTopic(StrictBase):
    """
    Output of normalize_topic.
    Field 'category' is the normalized, quiz-ready category string.
    """
    category: str = Field(description="Normalized quiz category (e.g., 'Gilmore Girls Characters', 'Type of Dog').")
    outcome_kind: Literal["characters", "types", "archetypes", "profiles"] = Field(
        description="What kind of outcomes the quiz should produce."
    )
    creativity_mode: Literal["whimsical", "balanced", "factual"] = Field(
        description="How creative/grounded the content should be."
    )
    rationale: str = Field(description="Brief explanation of the normalization decision.")
    intent: Opt[str] = Field(
        default=None,
        description="Broader intent classification (e.g., identify, sorting, alignment, compatibility, team_role, vibe, power_tier, timeline_era, career)."
    )


# ---------------------------------------------------------------------------
# Canonical agent state model (transport/storage)
# ---------------------------------------------------------------------------

class AgentGraphStateModel(StrictBase):
    """
    Canonical cache/transport model for agent state. Mirrors the GraphState keys
    used by the graph module. Strict, with typed history and questions.
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
    quiz_history: List[QuestionAnswer] = Field(default_factory=list)
    baseline_count: int = 0
    baseline_ready: bool = False
    ready_for_questions: bool = False
    should_finalize: Opt[bool] = None
    current_confidence: Opt[float] = None

    # Final result (kept loose to avoid API import cycles)
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


# ---------------------------------------------------------------------------
# Optional strict JSON Schema objects (usable with response_format='json_schema')
# ---------------------------------------------------------------------------

def build_initial_plan_jsonschema(category: Opt[str] = None) -> Dict[str, Any]:
    """
    JSON Schema for InitialPlan, dynamically derived from app config and (optionally)
    canonical sets for the given category (e.g., MBTI has 16 types).

    - minItems: settings.quiz.min_characters
    - maxItems: max(settings.quiz.max_characters, len(canonical_for(category)) if present)

    Pass `category` when you want the schema to widen for canonical topics.
    """
    # Defaults if settings is unavailable
    default_min = 2
    default_max = 32

    min_n = _quiz_setting("min_characters", default_min)
    max_n = _quiz_setting("max_characters", default_max)

    canon = canonical_for(category) if category else None
    canon_len = len(canon) if canon else None
    dynamic_max = max(max_n, canon_len) if canon_len else max_n

    return {
        "name": "InitialPlan",
        "strict": True,
        "schema": {
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
        },
    }


def build_synopsis_jsonschema() -> Dict[str, Any]:
    """Strict JSON Schema for Synopsis model."""
    return {
        "name": "Synopsis",
        "strict": True,
        "schema": {
            "title": "Synopsis",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "summary": {"type": "string"},
            },
            "required": ["title", "summary"],
        },
    }


def build_character_profile_jsonschema() -> Dict[str, Any]:
    """Strict JSON Schema for CharacterProfile model."""
    return {
        "name": "CharacterProfile",
        "strict": True,
        "schema": {
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
        },
    }


def build_question_option_jsonschema() -> Dict[str, Any]:
    """Schema for one selectable option in a question."""
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
    """Schema for a single QuestionOut, with dynamic option caps."""
    max_m = max(2, int(_quiz_setting("max_options_m", 4)))
    cap = int(max_options) if isinstance(max_options, int) and max_options > 0 else max_m
    return {
        "name": "QuestionOut",
        "strict": True,
        "schema": {
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
        },
    }


def build_question_list_jsonschema(
    *, count: Opt[int] = None, max_options: Opt[int] = None
) -> Dict[str, Any]:
    """Schema for QuestionList (array of QuestionOut)."""
    default_n = max(1, int(_quiz_setting("baseline_questions_n", 5)))
    n = int(count) if isinstance(count, int) and count > 0 else default_n
    return {
        "name": "QuestionList",
        "strict": True,
        "schema": {
            "title": "QuestionList",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "questions": {
                    "type": "array",
                    "items": build_question_out_jsonschema(max_options=max_options),
                    "minItems": n,
                    "maxItems": max(n, default_n) + 5,  # allow a small buffer
                }
            },
            "required": ["questions"],
        },
    }


def build_character_archetype_list_jsonschema(category: Opt[str] = None) -> Dict[str, Any]:
    """
    Schema for CharacterArchetypeList (array of strings), with dynamic bounds
    similar to InitialPlan.
    """
    default_min = _quiz_setting("min_characters", 2)
    default_max = _quiz_setting("max_characters", 32)
    canon = canonical_for(category) if category else None
    canon_len = len(canon) if canon else None
    dyn_max = max(default_max, canon_len) if canon_len else default_max

    return {
        "name": "CharacterArchetypeList",
        "strict": True,
        "schema": {
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
        },
    }


def build_character_casting_decision_jsonschema() -> Dict[str, Any]:
    """Schema for CharacterCastingDecision selection output."""
    base_array_any = {"type": "array", "items": {"type": "object"}}
    return {
        "name": "CharacterCastingDecision",
        "strict": True,
        "schema": {
            "title": "CharacterCastingDecision",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "reuse": base_array_any,
                "improve": base_array_any,
                "create": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["reuse", "improve", "create"],
        },
    }


def build_normalized_topic_jsonschema() -> Dict[str, Any]:
    """Schema for NormalizedTopic."""
    return {
        "name": "NormalizedTopic",
        "strict": True,
        "schema": {
            "title": "NormalizedTopic",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "category": {"type": "string", "minLength": 1},
                "outcome_kind": {
                    "type": "string",
                    "enum": ["characters", "types", "archetypes", "profiles"],
                },
                "creativity_mode": {
                    "type": "string",
                    "enum": ["whimsical", "balanced", "factual"],
                },
                "rationale": {"type": "string"},
                "intent": _nullable({"type": "string"}),
            },
            "required": ["category", "outcome_kind", "creativity_mode", "rationale"],
        },
    }


def build_next_step_decision_jsonschema() -> Dict[str, Any]:
    """Schema for NextStepDecision controller output."""
    return {
        "name": "NextStepDecision",
        "strict": True,
        "schema": {
            "title": "NextStepDecision",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": ["ASK_ONE_MORE_QUESTION", "FINISH_NOW"]},
                "winning_character_name": _nullable({"type": "string"}),
                "confidence": _nullable({"type": "number", "minimum": 0.0, "maximum": 1.0}),
            },
            "required": ["action"],
        },
    }


def build_safety_check_jsonschema() -> Dict[str, Any]:
    """Schema for SafetyCheck tool output."""
    return {
        "name": "SafetyCheck",
        "strict": True,
        "schema": {
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
        },
    }


def build_error_analysis_jsonschema() -> Dict[str, Any]:
    """Schema for ErrorAnalysis."""
    return {
        "name": "ErrorAnalysis",
        "strict": True,
        "schema": {
            "title": "ErrorAnalysis",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "retryable": {"type": "boolean"},
                "reason": {"type": "string"},
                "details": _nullable({"type": "object", "additionalProperties": {"type": "string"}}),
            },
            "required": ["retryable", "reason"],
        },
    }


def build_failure_explanation_jsonschema() -> Dict[str, Any]:
    """Schema for FailureExplanation."""
    return {
        "name": "FailureExplanation",
        "strict": True,
        "schema": {
            "title": "FailureExplanation",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "message": {"type": "string"},
                "tips": _nullable({"type": "array", "items": {"type": "string"}}),
            },
            "required": ["message"],
        },
    }


def build_image_prompt_jsonschema() -> Dict[str, Any]:
    """Schema for ImagePrompt enhancer output."""
    return {
        "name": "ImagePrompt",
        "strict": True,
        "schema": {
            "title": "ImagePrompt",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "negative_prompt": _nullable({"type": "string"}),
            },
            "required": ["prompt"],
        },
    }


# Back-compat: provide a config-driven default schema (no category widening).
# Prefer calling build_initial_plan_jsonschema(category) at use sites.
INITIAL_PLAN_JSONSCHEMA: Dict[str, Any] = build_initial_plan_jsonschema()

# Handy defaults for other frequent structured requests
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
# Registry: map tool_name → expected response model
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: Dict[str, Type[StrictBase]] = {
    # Planning / bootstrapping
    "initial_planner": InitialPlan,
    "topic_normalizer": NormalizedTopic,
    "character_list_generator": CharacterArchetypeList,
    "character_selector": CharacterCastingDecision,

    # Character pipelines
    "profile_writer": CharacterProfile,
    "profile_improver": CharacterProfile,

    # Question generation
    "question_generator": QuestionList,
    "next_question_generator": QuestionOut,

    # Misc / safety / diagnostics
    "safety_checker": SafetyCheck,
    "error_analyzer": ErrorAnalysis,
    "failure_explainer": FailureExplanation,
    "image_prompt_enhancer": ImagePrompt,

    # Adaptive flow controller
    "decision_maker": NextStepDecision,
}


def schema_for(tool_name: str):
    """Convenience accessor to look up the default response model for a tool."""
    return SCHEMA_REGISTRY.get(tool_name)


# ---------------------------------------------------------------------------
# Optional: registry of JSON Schema builders (for callers that prefer json_schema)
# ---------------------------------------------------------------------------

JSONSCHEMA_REGISTRY: Dict[str, Any] = {
    # Planning / bootstrapping
    "initial_planner": build_initial_plan_jsonschema,
    "topic_normalizer": build_normalized_topic_jsonschema,
    "character_list_generator": build_character_archetype_list_jsonschema,
    "character_selector": build_character_casting_decision_jsonschema,

    # Character pipelines
    "profile_writer": build_character_profile_jsonschema,
    "profile_improver": build_character_profile_jsonschema,

    # Question generation
    "question_generator": build_question_list_jsonschema,
    "next_question_generator": build_question_out_jsonschema,

    # Misc / safety / diagnostics
    "safety_checker": build_safety_check_jsonschema,
    "error_analyzer": build_error_analysis_jsonschema,
    "failure_explainer": build_failure_explanation_jsonschema,
    "image_prompt_enhancer": build_image_prompt_jsonschema,

    # Adaptive flow controller
    "decision_maker": build_next_step_decision_jsonschema,
}


def jsonschema_for(tool_name: str, **kwargs) -> Opt[Dict[str, Any]]:
    """
    Build and return the strict JSON Schema envelope for the given tool.
    Accepts optional kwargs to parameterize dynamic limits (e.g., category, count, max_options).
    """
    builder = JSONSCHEMA_REGISTRY.get(tool_name)
    if not builder:
        return None
    try:
        return builder(**kwargs) if kwargs else builder()
    except Exception:
        # Defensive: return None rather than raise so callers can fall back to Pydantic
        return None
