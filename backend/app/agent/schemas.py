"""
Agent ↔ LLM Schemas (strict, centralized)

Best practices implemented:
- Single StrictBase with `extra='forbid'` so models match schemas exactly.
- Tolerant `validation_alias` for common LLM key variants (e.g., synopsis_text).
- Separate "state shapes" (looser lists of dicts) from "structured output" variants
  returned by tools (e.g., QuestionOut with typed options).
- A SCHEMA_REGISTRY mapping tool_name → Pydantic model, used by callers that
  don't explicitly pass `response_model`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional as Opt, Type, Literal

from pydantic import BaseModel, Field
from pydantic import AliasChoices


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
    # Accept legacy synonyms that LLMs commonly produce
    summary: str = Field(
        default="",
        min_length=0,
        validation_alias=AliasChoices("summary", "synopsis_text", "synopsis"),
    )


class CharacterProfile(StrictBase):
    """One playable/guessable character."""
    name: str = Field(..., min_length=1)
    short_description: str = Field(default="", min_length=0)
    profile_text: str = Field(default="", min_length=0)
    image_url: Opt[str] = None


class QuizQuestion(StrictBase):
    """
    Question shape used by the agent state / API today.
    We keep options as List[Dict[str, str]] for maximum compatibility.
    """
    question_text: str = Field(..., min_length=1)
    options: List[Dict[str, str]]


# ---------------------------------------------------------------------------
# Structured-output variants (preferred for LLM responses)
# ---------------------------------------------------------------------------

class QuestionOption(StrictBase):
    text: str = Field(..., min_length=1, validation_alias=AliasChoices("text", "label", "option"))
    image_url: Opt[str] = None


class QuestionOut(StrictBase):
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
    archetypes: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("archetypes", "characters", "names", "labels"),
    )


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
    confidence: Opt[float] = None  # 0.0–1.0 (may come as 0–100 from some models)


# ---------------------------------------------------------------------------
# Planning tool models (centralized here for consistency)
# ---------------------------------------------------------------------------

class InitialPlan(StrictBase):
    """Output of the initial planning stage."""
    # Title can be omitted by providers; default it so the model validates.
    # Note: Optional alone does not give a default in Pydantic v2; the explicit
    # default (= None) prevents "Field required" errors. 
    title: Opt[str] = None
    synopsis: str = ""
    ideal_archetypes: List[str] = Field(default_factory=list)
    ideal_count_hint: Optional[int] = Field(
        default=None,
        description="Planner’s suggested number of outcomes (typ. 4–8, hard-cap 32)."
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
    intent: Optional[str] = Field(
        default=None,
        description="Broader intent classification (e.g., identify, sorting, alignment, compatibility, team_role, vibe, power_tier, timeline_era, career)."
    )


# ---------------------------------------------------------------------------
# Optional: canonical agent state model (transport/storage)
# ---------------------------------------------------------------------------

class AgentGraphStateModel(StrictBase):
    """
    Canonical cache/transport model for agent state. Mirrors the GraphState keys.
    We intentionally keep flexible dict types for a few fields because Redis
    round-trips can dehydrate Pydantic objects.
    """
    session_id: Any
    trace_id: str
    category: str

    # Store Redis-hydrated messages as dicts for compatibility
    messages: List[Dict[str, Any]] = Field(default_factory=list)

    is_error: bool = False
    error_message: Opt[str] = None
    error_count: int = 0

    rag_context: Opt[List[Dict[str, Any]]] = None
    outcome_kind: Opt[str] = None
    creativity_mode: Opt[str] = None

    category_synopsis: Opt[Synopsis] = None
    ideal_archetypes: List[str] = Field(default_factory=list)
    generated_characters: List[CharacterProfile] = Field(default_factory=list)
    generated_questions: List[QuizQuestion] = Field(default_factory=list)

    quiz_history: List[Dict[str, Any]] = Field(default_factory=list)
    baseline_count: int = 0
    baseline_ready: bool = False
    ready_for_questions: bool = False
    should_finalize: Opt[bool] = None
    current_confidence: Opt[float] = None

    final_result: Opt[Dict[str, Any]] = None  # keep loose here to avoid API import cycles
    last_served_index: Opt[int] = None


# ---------------------------------------------------------------------------
# Optional strict JSON Schema objects (usable with response_format='json_schema')
# ---------------------------------------------------------------------------

INITIAL_PLAN_JSONSCHEMA = {
  "name": "InitialPlan",
  "schema": {
    "title": "InitialPlan",
    "description": "Output of the initial planning stage.",
    "type": "object",
    "additionalProperties": False,
    "properties": {
      "title": {"type": "string", "description": "Catchy quiz title."},
      "synopsis": {"type": "string", "description": "Engaging synopsis (2–3 sentences)."},
      "ideal_archetypes": {"type": "array", "items": {"type": "string"}, "description": "4–6 archetypes."}
    },
    "required": ["synopsis", "ideal_archetypes"]
  },
  "strict": True,
}


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
