# backend/app/agent/prompts.py
"""
Agent Prompts

This module centralizes all prompt engineering for the Quizzical AI agent.
It implements a resilient fetch-or-fallback strategy. Prompts are first
looked for in the application's dynamic configuration. If not found, they
fall back to the hardcoded defaults defined in this file.

This version aligns with the updated graph/tooling and hydration logic:
- Uses {category} as the canonical placeholder (no {raw_topic}/{normalized_category}).
- Topic normalizer returns {"category", "outcome_kind", "creativity_mode", "rationale"}.
- Character list generator returns a JSON array of strings (not wrapped in an object).
- Profile writer/improver use snake_case keys compatible with CharacterProfile.
- Question generators return objects with {"question_text", "options[{text,image_url?}]"}.
- Decision maker returns a JSON object with {"action","confidence","winning_character_name"}.
- NEW: Optional retrieval fields:
    * {search_context} for topic normalization and character list generation
    * {character_context} for character/profile writing
  These are OPTIONAL; prompts must behave sensibly when they are empty.
"""

from typing import Dict, Tuple

import structlog
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings

logger = structlog.get_logger(__name__)

# =============================================================================
# Default Prompt Registry
# =============================================================================

DEFAULT_PROMPTS: Dict[str, Tuple[str, str]] = {
    # --- Topic normalization / interpretation --------------------------------
    "topic_normalizer": (
        "You are a meticulous topic normalizer for a BuzzFeed-style personality quiz.",
        "Normalize the user-provided quiz topic. If search context is present, use it to disambiguate, "
        "but prefer clear, general rules for speed and determinism.\n\n"
        "## Search Context (optional)\n{search_context}\n\n"
        "## User Topic\n{category}\n\n"
        "Decide the following and return ONLY this JSON object:\n"
        "{\n"
        '  "category": string,                    // e.g., "Gilmore Girls Characters", "Type of Dog", "Myers-Briggs Personality Types"\n'
        '  "outcome_kind": "characters" | "types" | "archetypes" | "profiles",\n'
        '  "creativity_mode": "whimsical" | "balanced" | "factual",\n'
        '  "rationale": string                    // one brief sentence explaining your choice\n'
        "}\n\n"
        "Rules to apply:\n"
        "- Media/franchise titles -> append 'Characters' and set outcome_kind='characters'.\n"
        "- Plain/plural nouns -> 'Type of <Singular>' and outcome_kind='types'.\n"
        "- Serious/established frameworks (MBTI, DISC, doctor specialties, etc.) ->\n"
        "  outcome_kind='profiles' or 'types' and creativity_mode='factual'.\n"
        "- If unclear, pick between 'archetypes' or 'types' and set creativity_mode='balanced'."
    ),

    # --- Planning and Strategy Prompts ---------------------------------------
    "initial_planner": (
        "You are a master planner for viral personality quizzes.",
        "Plan a BuzzFeed-style personality quiz about '{category}'.\n"
        "Outcome kind: {outcome_kind}. Creativity mode: {creativity_mode}.\n\n"
        "Return a brief plan that lists:\n"
        "1) A 2–3 sentence concept for how the quiz determines “what {category} you are”.\n"
        "2) The intended tone (whimsical vs factual) and why it fits.\n"
        "3) 4–6 outcome labels (names only) that are distinct and cover the category space."
    ),

    # --- Archetype/Outcome list generation (names only; returns ARRAY) -------
    "character_list_generator": (
        "You are a world-class quiz architect who enumerates distinct outcomes.",
        "Given the quiz concept and optional search context below, output 4–6 distinct outcome NAMES.\n"
        "Adapt creativity to Creativity mode: {creativity_mode}. If factual or a known media/framework, "
        "use the context to extract canonical names ONLY (no inventions). If no useful context is available, "
        "generate distinct, useful labels consistent with the synopsis and creativity mode.\n\n"
        "## Creativity Mode\n{creativity_mode}\n\n"
        "## Search Context (optional)\n{search_context}\n\n"
        "## QUIZ CATEGORY\n{category}\n\n"
        "## QUIZ SYNOPSIS\n{synopsis}\n\n"
        "Return only a JSON array of strings, e.g.: [\"name1\", \"name2\", \"name3\"]."
    ),

    # --- Retrieval/selection stays conceptual (reuse/improve/create) ----------
    "character_selector": (
        "You are a casting director matching ideal outcomes to available profiles.",
        "Quiz: {category}\n"
        "Ideal outcomes:\n{ideal_archetypes}\n\n"
        "Available profiles (from memory/RAG):\n{retrieved_characters}\n\n"
        "For each ideal outcome, choose to 'reuse', 'improve', or 'create'.\n"
        "Return JSON with keys 'reuse', 'improve', 'create'; each is a list of items with mapping to the ideal outcome name and any improvement notes."
    ),

    # --- Synopsis/title tuned to BuzzFeed-style with adaptive tone ------------
    "synopsis_generator": (
        "You write irresistible BuzzFeed-style quiz copy and adjust tone by context.",
        "Create a synopsis for a personality quiz about '{category}'.\n"
        "Outcome kind: {outcome_kind}. Creativity mode: {creativity_mode}.\n\n"
        "Return ONLY this JSON object:\n"
        "{\n"
        '  "title": string,      // catchy, defaulting to: "What {category} Are You?" if unsure\n'
        '  "summary": string     // 2–3 sentences; playful if whimsical, precise if factual\n'
        "}"
    ),

    # --- Detailed profile writing (short + long) ------------------------------
    "profile_writer": (
        "You craft outcome profiles for personality quizzes.\n"
        "Short description must be immediately useful and concrete.\n"
        "Long description must be exact enough to guide question + answer creation.",
        "Write a profile for quiz '{category}' in creativity mode '{creativity_mode}'.\n"
        "Outcome name: '{character_name}'. Outcome kind: {outcome_kind}.\n"
        "Keep the outcome name exactly as provided. If context is present, base your writing ONLY on that context "
        "(do not invent or contradict it). If context is empty, create a plausible, coherent profile consistent with "
        "the category and creativity mode.\n\n"
        "## Context (optional)\n{character_context}\n\n"
        "Return ONLY this JSON object (snake_case keys):\n"
        "{\n"
        '  "name": "{character_name}",\n'
        '  "short_description": string,   // one crisp sentence, highly informative\n'
        '  "profile_text": string,        // 2–4 paragraphs; concrete traits, tendencies, preferences, pitfalls\n'
        '  "image_url": string | null     // optional\n'
        "}"
    ),

    # --- Profile improver (kept compatible, snake_case) -----------------------
    "profile_improver": (
        "You are a precise profile editor. Fix clarity, coverage, and usefulness.",
        "Improve the following profile using the feedback. Keep the same outcome name.\n\n"
        "EXISTING:\n{existing_profile}\n\n"
        "FEEDBACK:\n{feedback}\n\n"
        "Return ONLY the full updated JSON profile object (same snake_case schema as profile_writer)."
    ),

    # --- Baseline question generator ------------------------------------------
    # Requirements:
    #  • Generate N diverse questions that together give each outcome a fair, equal shot.
    #  • Each question must have 2..max_options options.
    #  • Options should map meaningfully to different outcomes (not trivially the same).
    #  • No rephrasings; cover different facets (values, behaviors, preferences).
    "question_generator": (
        "You are a psychologist/game-designer generating *baseline* questions for a personality quiz.",
        "Create EXACTLY {count} diverse multiple-choice baseline questions for '{category}'.\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}.\n"
        "Context:\n"
        "• SYNOPSIS: {synopsis}\n"
        "• OUTCOME PROFILES: {character_profiles}\n\n"
        "Design goals:\n"
        "- Make the baseline as *scientific* as possible for forming an initial posterior where each outcome has ~equal likelihood after all baseline answers.\n"
        "- Questions must explore distinct dimensions (values, habits, preferences, constraints), not restate each other.\n"
        "- Each question MUST have between 2 and {max_options} options.\n"
        "- Options should be well-differentiated and plausibly indicative of different outcomes.\n\n"
        "Return EXACTLY {count} questions as a JSON array of objects with this schema (no extra fields):\n"
        "[\n"
        "  {\n"
        '    "question_text": string,\n'
        '    "options": [\n'
        '      {"text": string, "image_url": string (optional)},\n'
        "      ...  // 2..{max_options} items\n"
        "    ]\n"
        "  }, ...\n"
        "]"
    ),

    # --- Next-question generator (adaptive) -----------------------------------
    # The next question must do exactly one of the strategies:
    #  1) Randomized exploration of vague/low-signal areas from baseline.
    #  2) Test-the-negative of the current best guess (disconfirmatory).
    #  3) Further-narrow a close contest between top candidates.
    # Keep 2..max_options answers.
    "next_question_generator": (
        "You are an adaptive quiz engine choosing the most informative *next* question.",
        "Generate ONE new multiple-choice question for '{category}' now.\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}.\n\n"
        "Inputs:\n"
        "• SYNOPSIS: {synopsis}\n"
        "• OUTCOME PROFILES: {character_profiles}\n"
        "• QUIZ HISTORY (Q&A so far): {quiz_history}\n\n"
        "Pick exactly ONE strategy for this question:\n"
        "  (1) Randomized exploration to probe vague areas from baseline\n"
        "  (2) Test-the-negative of the current best guess\n"
        "  (3) Narrow between the top remaining candidates\n\n"
        "Constraints:\n"
        "- The question must be novel (not a rephrase).\n"
        "- Provide between 2 and {max_options} options.\n"
        "- Options must be meaningfully distinct.\n\n"
        "Return exactly ONE object in this JSON schema (no extra commentary):\n"
        "{\n"
        '  "question_text": string,\n'
        '  "options": [\n'
        '    {"text": string, "image_url": string (optional)},\n'
        "    ...  // 2..{max_options} items\n"
        "  ]\n"
        "}"
    ),

    # --- Decision prompt to finish early or continue --------------------------
    "decision_maker": (
        "You decide whether to ask another question or finish with a result.",
        "Quiz: '{category}'\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}.\n\n"
        "Given the profiles and Q&A history:\n"
        "• PROFILES: {character_profiles}\n"
        "• HISTORY: {quiz_history}\n\n"
        "Return ONLY this JSON object (no prose):\n"
        "{\n"
        '  "action": "ASK_ONE_MORE_QUESTION" | "FINISH_NOW",\n'
        '  "confidence": number,              // 0..1; if you think in %, divide by 100\n'
        '  "winning_character_name": string   // best guess; "" if asking another question\n'
        "}"
    ),

    # --- Final result writer ---------------------------------------------------
    "final_profile_writer": (
        "You write personalized, uplifting, and insightful personality results.",
        "User matched: '{winning_character_name}' for quiz '{category}'.\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}.\n"
        "History:\n{quiz_history}\n\n"
        "Write the result starting with the title:\n"
        "'You are The {winning_character_name}!'\n\n"
        "Then explain *why* their answers fit this profile. Keep it friendly and clear; avoid over-claiming."
    ),

    # --- Image helper ---------------------------------------------------------
    "image_prompt_enhancer": (
        "You are an expert prompt engineer for text-to-image models.",
        "Expand this concept into a vivid, single-line prompt (comma-separated descriptors). Style: '{style}'.\n"
        "Concept: {concept}"
    ),

    # --- Safety / Analysis / Failures ----------------------------------------
    "safety_checker": (
        "You are a safety classification expert. Respond with only 'safe' or 'unsafe'.",
        "Classify this quiz topic/synopsis for safety:\nTopic: {category}\nSynopsis: {synopsis}"
    ),
    "error_analyzer": (
        "You are a senior AI engineer debugging a stateful agent.",
        "Analyze the error and propose ONE concrete next action.\n\n"
        "ERROR:\n{error_message}\n\n"
        "STATE:\n{state}"
    ),
    "failure_explainer": (
        "You are a friendly support specialist for a fun quiz app.",
        "The quiz generation failed because: '{error_summary}'.\n"
        "Write a short, friendly apology without technical jargon and suggest trying a different topic."
    ),
}

# =============================================================================
# Prompt Manager Service (unchanged)
# =============================================================================

class PromptManager:
    """A service to manage and retrieve prompt templates."""

    def get_prompt(self, prompt_name: str) -> ChatPromptTemplate:
        """
        Retrieves a prompt template by name.

        It first checks the dynamic app settings. If not found,
        it falls back to the local DEFAULT_PROMPTS registry.
        """
        prompt_config = settings.llm_prompts.get(prompt_name)

        if prompt_config and prompt_config.system_prompt and prompt_config.user_prompt_template:
            logger.debug("Loading prompt from dynamic configuration", prompt_name=prompt_name)
            system_template = prompt_config.system_prompt
            human_template = prompt_config.user_prompt_template
        else:
            logger.debug("Prompt not in config, falling back to default", prompt_name=prompt_name)
            if prompt_name not in DEFAULT_PROMPTS:
                raise ValueError(f"Prompt '{prompt_name}' not found in dynamic config or defaults.")
            system_template, human_template = DEFAULT_PROMPTS[prompt_name]

        return ChatPromptTemplate.from_messages(
            [
                ("system", system_template),
                ("human", human_template),
            ]
        )

# Singleton instance
prompt_manager = PromptManager()
