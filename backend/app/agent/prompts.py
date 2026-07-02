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
- **Initial planner now returns {"title","synopsis","ideal_archetypes"} in a single call.**
- Character list generator returns a JSON object: {"archetypes": [...] }.
- Profile writer/improver use snake_case keys compatible with CharacterProfile.
- **NEW:** Added a batch profile writer prompt ("profile_batch_writer") that returns an
  array of CharacterProfile-shaped JSON objects in one call.
- Question generators return objects with {"question_text", "options[{text,image_url?}]"}.
- Decision maker returns a JSON object with {"action","confidence","winning_character_name"}.
- Optional retrieval fields:
    * {search_context} for topic normalization and character list generation
    * {character_context} and {character_contexts} for character/profile writing
  These are OPTIONAL; prompts must behave sensibly when they are empty.
"""


import structlog
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings

logger = structlog.get_logger(__name__)

# =============================================================================
# Default Prompt Registry
# =============================================================================

# IMPORTANT: All literal JSON braces are escaped as {{ }} so LangChain does not
# interpret them as template variables (which caused the '\n    "question_text"' KeyError).

DEFAULT_PROMPTS: dict[str, tuple[str, str]] = {
    # --- Topic normalization / interpretation --------------------------------
    "topic_normalizer": (
        "You are a meticulous topic normalizer for a BuzzFeed-style personality quiz.",
        "Normalize the user-provided quiz topic. If search context is present, use it to disambiguate, "
        "but prefer clear, general rules for speed and determinism.\n\n"
        "## Search Context (optional)\n{search_context}\n\n"
        "## User Topic\n{category}\n\n"
        "Decide the following and return ONLY this JSON object:\n"
        "{{\n"
        '  "category": string,                    // e.g., "Gilmore Girls Characters", "Type of Dog", "Myers-Briggs Personality Types"\n'
        '  "outcome_kind": "characters" | "types" | "archetypes" | "profiles",\n'
        '  "creativity_mode": "whimsical" | "balanced" | "factual",\n'
        '  "rationale": string,                   // one brief sentence explaining your choice\n'
        '  "intent": string                       // one of: "identify" (default), "sorting", "alignment", "compatibility", "team_role", "vibe", "power_tier", "timeline_era", "career"\n'
        "}}\n\n"
        "Rules to apply:\n"
        "- Media/franchise titles -> append 'Characters' and set outcome_kind='characters'.\n"
        "- Plain/plural nouns -> 'Type of <Singular>' and outcome_kind='types'.\n"
        "- Serious/established frameworks (MBTI, DISC, doctor specialties, etc.) ->\n"
        "  outcome_kind='profiles' or 'types' and creativity_mode='factual'.\n"
        "- If unclear, pick between 'archetypes' or 'types' and set creativity_mode='balanced'.\n"
        "\n- Intent guidance: 'identify' when mapping to a single entity; 'sorting' for houses/factions; 'alignment' for ethical axes; 'compatibility' for pairing/matching; 'team_role' for workplace/party roles; 'vibe' for aesthetic/core; 'power_tier' for rankings; 'timeline_era' for era/style; 'career' for vocational types."
    ),

    # --- Planning and Strategy Prompts ---------------------------------------
    # Updated: returns title + synopsis + archetype names in one shot.
    "initial_planner": (
        "You are a master planner for viral personality quizzes.",
        "Plan a BuzzFeed-style personality quiz about '{category}'.\n"
        "Respect the literal subject of '{category}'. If the category names a\n"
        "specific dimension/subgroup of a fictional universe (e.g., a race,\n"
        "house, faction, side, alignment, class, species, region, district,\n"
        "Ajah, bender nation, Hogwarts house, Pok\u00e9mon type, etc.),\n"
        "return instances/members of THAT dimension \u2014 NOT the franchise's\n"
        "main characters. For example 'Lord of the Rings Race' -> Hobbits,\n"
        "Elves, Dwarves, Men (not Frodo/Gandalf); 'Harry Potter House' -> the\n"
        "four houses. Only return proper names of characters/artists/teams when\n"
        "the category itself implies that (e.g., 'Wheel of Time character',\n"
        "'NBA player') or is the franchise alone with NO dimension qualifier.\n"
        "When unsure, mirror the user's noun.\n"
        "Outcome kind: {outcome_kind} (a value of 'dimension' means: return "
        "members of the named dimension, not characters). "
        "Creativity mode: {creativity_mode}. User intent: {intent}.\n\n"
        "If a canonical list is provided, return it exactly and in the same order.\n"
        "Canonical (optional): {canonical_names}\n\n"
        "Return ONLY this JSON object:\n"
        "{{\n"
        '  "title": string,                 // catchy; default to "What {category} Are You?" if unsure\n'
        '  "synopsis": string,              // 3–4 sentences; playful if whimsical, precise if factual\n'
        '  "ideal_archetypes": string[],    // Specific and relevant labels/characters/types\n'
        '  "ideal_count_hint": number       // More is OK\n'
        "}}"
    ),

    # --- Archetype/Outcome list generation (names only; returns OBJECT) -------
    "character_list_generator": (
        "You are a world-class quiz architect who enumerates distinct outcomes.",
        "Given the quiz concept and optional search context below, output distinct outcome NAMES.\n"
        "Mirror the user's subject noun. If the category names a dimension/\n"
        "subgroup (races, houses, factions, sides, alignments, classes,\n"
        "species, regions, districts, Ajahs, bending nations, Hogwarts houses,\n"
        "Pok\u00e9mon types, MBTI types, etc.), return INSTANCES/members of that\n"
        "dimension \u2014 not the franchise's main characters (e.g. 'Lord of the\n"
        "Rings Race' -> Hobbits, Elves, Dwarves, Men; not Frodo/Gandalf). Only\n"
        "return proper nouns (specific named characters/artists/teams) when the\n"
        "category itself explicitly implies a roster of named people.\n"
        "Select an appropriate number of outcomes for the topic. Adapt to Creativity mode: {creativity_mode}. User intent: {intent}.\n"
        "If factual or a known media/framework/set and context is present, base labels on that context; otherwise generate plausible, relevant labels consistent with the synopsis and intent.\n\n"
        "## Creativity Mode\n{creativity_mode}\n\n"
        "## Search Context (optional)\n{search_context}\n\n"
        "## Canonical Names (optional)\n{canonical_names}\n\n"
        "## QUIZ CATEGORY\n{category}\n\n"
        "## QUIZ SYNOPSIS\n{synopsis}\n\n"
        "If canonical names are provided, prefer them verbatim and in the same order.\n\n"
        "Return ONLY this JSON object:\n"
        "{{\n"
        '  "archetypes": ["name1", "name2", "name3"]\n'
        "}}"
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
        "{{\n"
        '  "title": string,      // catchy, defaulting to: "What {category} Are You?" if unsure\n'
        '  "summary": string     // 2–3 sentences; playful if whimsical, precise if factual\n'
        "}}"
    ),

    # --- Detailed profile writing (short + long) ------------------------------
    "profile_writer": (
        "You craft outcome profiles for personality quizzes.\n"
        "Short description must be immediately useful and concrete.\n"
        "Long description must be exact enough to guide question + answer creation.",
        "Write a profile for quiz '{category}' in creativity mode '{creativity_mode}'. Intent: {intent}.\n"
        "Outcome name: '{character_name}'. Outcome kind: {outcome_kind}.\n"
        "Keep the outcome name exactly as provided. If context is present, base your writing ONLY on that context "
        "(do not invent or contradict it). If context is empty, create a plausible, coherent profile consistent with "
        "the category and creativity mode.\n\n"
        "## Context (optional)\n{character_context}\n\n"
        "Return ONLY this JSON object (snake_case keys):\n"
        "{{\n"
        '  "name": "{character_name}",\n'
        '  "short_description": string,   // 1–2 complete sentences, 80–180 chars, no trailing ellipsis (hard cap 240)\n'
        '  "profile_text": string,        // 2–4 paragraphs; concrete traits, tendencies, preferences, pitfalls\n'
        '  "image_url": string | null     // optional\n'
        "}}"
    ),

    # --- NEW: Batch profile writer (array of CharacterProfile JSON) ----------
    "profile_batch_writer": (
        "You craft vivid, distinctive quiz outcome profiles in batch. Two things "
        "matter equally. (1) COMPLETENESS: return exactly one profile for EVERY "
        "name you are given — never drop, merge, summarise, or skip a name, even "
        "if the batch is long or some names feel similar. (2) QUALITY: each "
        "profile must feel specific and true to that outcome — concrete, vivid, "
        "and clearly distinct from the others, never generic horoscope filler "
        "that could apply to anyone.",
        "Quiz: {category}\n"
        "Outcome kind: {outcome_kind}\n"
        "Creativity: {creativity_mode}\n"
        "Intent: {intent}\n\n"
        "If context is provided, use it strictly (no invention). Otherwise, write plausible, coherent profiles.\n\n"
        "## Optional Context (may be empty)\n{character_contexts}\n\n"
        "## REQUIRED OUTCOMES — write EXACTLY {count} profiles, one per name\n"
        "Write a profile for every one of these {count} names, in this exact "
        "order (do not add, drop, merge, or reorder them):\n"
        "{character_names}\n\n"
        "For EACH outcome:\n"
        "- short_description: 1–2 complete sentences, 80–180 characters, no trailing ellipsis (hard cap 240).\n"
        "- profile_text: 2\u20133 substantial paragraphs (roughly 120\u2013220 words total).\n"
        "  Cover concrete traits, tendencies, preferences, strengths, and pitfalls.\n"
        "  Address the reader in the second person (\"You\u2026\"). No bullet lists.\n\n"
        "QUALITY BAR (each profile must be genuinely good, not merely present):\n"
        "- Name 2\u20133 CONCRETE, specific traits or behaviours a fan would instantly\n"
        "  recognise for THIS outcome \u2014 not vague adjectives. Avoid empty filler\n"
        "  like \"unique\", \"special\", \"complex\", \"one of a kind\", \"a true original\".\n"
        "- Each profile must be clearly DIFFERENTIATED from its siblings: if you\n"
        "  could swap two names and the text still fits, it is too generic \u2014 rewrite it.\n"
        "- Prefer vivid specifics over abstractions; no clich\u00e9s, no hedging.\n"
        "- Match the Creativity mode and Outcome kind stated above.\n\n"
        "COMPLETENESS CONTRACT (non-negotiable):\n"
        "- The output array MUST contain EXACTLY {count} objects — no more, no fewer.\n"
        "- There MUST be exactly one object whose \"name\" matches each listed "
        "name VERBATIM (same spelling, casing, and punctuation).\n"
        "- Keep every profile complete; if you are running low on space, write "
        "shorter profiles rather than dropping any name. Never truncate the array.\n"
        "- Before finishing, count your objects and confirm the total equals "
        "{count} and that every listed name appears exactly once.\n"
        "Return ONLY a JSON array of exactly {count} objects with this exact schema:\n"
        "[\n"
        "  {{\n"
        '    "name": string,\n'
        '    "short_description": string,\n'
        '    "profile_text": string,\n'
        '    "image_url": string | null\n'
        "  }}, ...\n"
        "]"
    ),

    # --- Baseline question generator ------------------------------------------
    # Requirements:
    #  • Generate N diverse questions that together give each outcome a fair, equal shot.
    #  • Each question must have 2..max_options options.
    #  • Options should map meaningfully to different outcomes (not trivially the same).
    #  • No rephrasings; cover different facets (values, behaviors, preferences).
    "question_generator": (
        "You are a psychologist/researcher generating baseline questions for a personality quiz.",
        "Create EXACTLY {count} diverse multiple-choice baseline questions for '{category}'.\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}. Intent: {intent}.\n"
        "Context:\n"
        "• SYNOPSIS: {synopsis}\n"
        "• OUTCOME PROFILES: {character_profiles}\n\n"
        "Design goals:\n"
        "- Make the baseline as scientific as possible for forming an initial posterior where each outcome has ~equal likelihood after all baseline answers.\n"
        "- Questions must explore distinct dimensions, not restate each other.\n"
        "- Choose an appropriate number of answer options based on the questions, two, three, or up to {max_options} options.\n"
        "- Options should be well-differentiated and plausibly indicative of different outcomes.\n\n"
        "ABSOLUTELY FORBIDDEN (these defeat the quiz — YOU must infer the match, never the user):\n"
        "- Do NOT ask the user which outcome/character/type they think they are, match, identify "
        "with, relate to, resemble, or would pick. The user must NEVER self-identify, guess, rank, "
        "or predict their own result.\n"
        "- Do NOT name the candidate outcomes/characters in the question text or the options, and do "
        "NOT ask the user to choose among, rank, or rate them.\n"
        "- Do NOT ask meta questions about the quiz, the result, this app, or how the matching works "
        "(e.g. 'Which of these characters do you feel you match with?', 'Which result do you want?', "
        "'How accurate do you think this quiz is?').\n"
        "- Every question must be an ordinary preference, personality, behaviour, value, or situational "
        "question whose ANSWERS let you (the agent) infer the match. The user answers about themselves, "
        "not about the outcomes.\n\n"
        "Return ONLY this JSON object (no extra fields):\n"
        "{{\n"
        '  "questions": [\n'
        "    {{\n"
        '      "question_text": string,\n'
        '      "options": [\n'
        '        {{"text": string, "image_url": string (optional)}},\n'
        "        ...  // 2..{max_options} items\n"
        "      ]\n"
        "    }},\n"
        "    ... // exactly {count} items\n"
        "  ]\n"
        "}}"
    ),

    # --- Next-question generator (adaptive) -----------------------------------
    # The next question must do exactly one of the strategies:
    #  1) Randomized exploration of vague/low-signal areas from baseline.
    #  2) Test-the-negative of the current best guess (disconfirmatory).
    #  3) Further-narrow a close contest between top candidates.
    # Keep 2..max_options answers.
    "next_question_generator": (
        "You are a psychologist/researcher creating a question and possible answer options that will result in the most information gain about the user's personality.",
        "Generate ONE new multiple-choice question for '{category}' now.\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}. Intent: {intent}.\n\n"
        "Inputs:\n"
        "• SYNOPSIS: {synopsis}\n"
        "• OUTCOME PROFILES: {character_profiles}\n"
        "• QUIZ HISTORY (Q&A so far): {quiz_history}\n\n"
        "Pick exactly ONE strategy for this question based to maximize your understanding of the users personality:\n"
        "  (1) Exploration to probe vague areas\n"
        "  (2) Test-the-negative of the current best guess\n"
        "  (3) Narrow between the top remaining candidates\n\n"
        "Constraints:\n"
        "- The question and answer options must be novel (not a rephrase of any previous question or answer options).\n"
        "- Provide 2, 3 or up to {max_options} options\n"
        "- Options must be meaningfully distinct.\n\n"
        "ABSOLUTELY FORBIDDEN (these defeat the quiz — YOU must infer the match, never the user):\n"
        "- Do NOT ask the user which outcome/character/type they think they are, match, identify "
        "with, relate to, resemble, or would pick. The user must NEVER self-identify, guess, rank, "
        "or predict their own result.\n"
        "- Do NOT name the candidate outcomes/characters in the question text or the options, and do "
        "NOT ask the user to choose among, rank, or rate them.\n"
        "- Do NOT ask meta questions about the quiz, the result, this app, or how the matching works "
        "(e.g. 'Which of these characters do you feel you match with?', 'Which result do you want?').\n"
        "- The question must be an ordinary preference, personality, behaviour, value, or situational "
        "question whose ANSWER gives you information to infer the match.\n\n"
        "Design goals:\n"
        "- Serious topics should have serious questions and answer options; whimsical topics should have whimsical questions and answer options.\n"
        "- Questions and answer options should be succinct and short.\n "
        "Return exactly ONE object in this JSON schema (no extra commentary):\n"
        "{{\n"
        '  "question_text": string,\n'
        '  "options": [\n'
        '    {{"text": string, "image_url": string (optional)}},\n'
        "    ...  // 2..{max_options} items\n"
        "  ]\n"
        "}}"
    ),

    # --- Decision prompt to finish early or continue --------------------------
    "decision_maker": (
        "You are a psychologist/researcher and you analyze quiz answers and recommend whether to ask one more question or finish.",
        "Quiz: '{category}'\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}.\n\n"
        "Context:\n"
        "• PROFILES: {character_profiles}\n"
        "• HISTORY: {quiz_history}\n\n"
        "Generally, we'll need at least as many questions as there are profiles to get a good signal.\n"
        "Highly creative and whimsical quiz topics should generally be less strict than factual ones.\n"
        "Quiz topics of a serious nature (e.g., mental health, career guidance) should be more strict.\n"
        "Constraints (for your awareness):\n"
        "- The system will FORCE FINISH at {max_total_questions} total answers.\n"
        "- The system may FINISH EARLY only if total answers ≥ {min_questions_before_finish} AND confidence ≥ {confidence_threshold}.\n\n"
        "Return ONLY this JSON (no extra words):\n"
        "{{\n"
        '  "action": "ASK_ONE_MORE_QUESTION" | "FINISH_NOW",\n'
        '  "confidence": number,              // 0..1; if you think in %, divide by 100\n'
        '  "winning_character_name": string   // best guess; "" if asking another question\n'
        "}}"
    ),

    # --- Final result writer ---------------------------------------------------
    "final_profile_writer": (
        "You write personalized, insightful personality readings that feel both fun and substantive. "
        "You write in the second person, weave in concrete evidence from the user's answers, and avoid generic platitudes. "
        "You ALWAYS produce at least 3 substantial paragraphs separated by a single blank line. "
        "You NEVER hand back a one-paragraph or two-paragraph reading \u2014 the entire quiz culminates here, "
        "so a thin description is the worst possible outcome.",
        "User matched: '{winning_character_name}' for quiz '{category}'.\n"
        "Creativity mode: {creativity_mode}. Outcome kind: {outcome_kind}.\n"
        "History (each item is a question + the answer the user picked):\n{quiz_history}\n\n"
        "Write a deep personality reading addressed to the user (\"You\u2026\").\n"
        "HARD REQUIREMENTS for `description` (these are non-negotiable):\n"
        "  \u2022 At least 3 paragraphs (target 4); upper bound 5. Roughly 300\u2013500 words total.\n"
        "  \u2022 At least 400 characters total. Anything shorter will be rejected.\n"
        "  \u2022 Paragraphs MUST be separated by a single blank line (\\n\\n). Do not run paragraphs together.\n"
        "  \u2022 Paragraph 1: who this outcome is and why it fits THIS user, with at least one concrete reference to an answer they gave.\n"
        "  \u2022 Middle paragraphs (2 of them, minimum): two distinct dimensions of their personality (values, behaviour patterns, likely strengths, likely growth edges); include at least one additional concrete answer reference (2+ total references where history permits).\n"
        "  \u2022 Final paragraph: a forward-looking note \u2014 how this profile shows up day-to-day, what to lean into, what to watch out for.\n"
        "Tone matches creativity_mode: whimsical \u2192 playful but specific; balanced \u2192 warm and grounded; factual \u2192 measured and evidence-led.\n"
        "Do NOT use bullet lists, headings, or markdown. Plain paragraphs separated by blank lines.\n"
        "Do NOT hedge with phrases like 'maybe' or 'perhaps' \u2014 commit to the reading.\n\n"
        "Return ONLY this JSON object (no extra text, no code fences):\n"
        "{{\n"
        '  "title": "You are <the / an / a / blank> {winning_character_name}!",  // pick the linguistically correct article\n'
        '  "description": string,      // 3\u20135 paragraphs, \u2265400 chars, plain text with blank-line paragraph breaks\n'
        '  "image_url": string | null  // null if unsure\n'
        "}}\n"
    ),

    # --- Blended-profile writer (DISC pilot etc.) -----------------------------
    # For a BLENDED framework (e.g. DISC) the user's outcome is a PROFILE across
    # the canonical dimensions, NOT one-of-N. This writer reads the quiz answers
    # and produces a per-dimension emphasis blend + a cohesive narrative that
    # explains the primary/secondary blend. Gated to an allowlist (DISC only by
    # default); every other topic still uses ``final_profile_writer``.
    "blended_profile_writer": (
        "You are an expert at reading personality FRAMEWORKS that resolve to a BLEND, not a single label. "
        "For frameworks like DISC, a real result is a PROFILE across the dimensions — a primary style with a "
        "supporting secondary (e.g. \"D/C\") and relative emphasis — never just one of the four. "
        "You write in the second person, cite concrete evidence from the user's answers, and explain how the "
        "dimensions COMBINE for this specific person. You never reduce the person to a single dimension.",
        "Framework: '{category}'. This is a BLENDED outcome.\n"
        "Canonical dimensions (use EXACTLY these names, all of them, in this order): {dimension_names}\n"
        "Creativity mode: {creativity_mode}.\n"
        "History (each item is a question + the answer the user picked):\n{quiz_history}\n\n"
        "Produce a blended profile reading addressed to the user (\"You…\").\n"
        "HARD REQUIREMENTS (non-negotiable):\n"
        "  • `dimensions`: one entry per canonical dimension above — ALL of them, names spelled EXACTLY as given.\n"
        "  • Each dimension has an integer `emphasis` 0–100 reflecting how strongly the user's answers lean that way "
        "(these are a relative emphasis, they need NOT sum to 100) and a one-sentence `blurb` grounded in their answers.\n"
        "  • `primary`: the highest-emphasis dimension name. `secondary`: the next strongest (or null if the profile is near-flat).\n"
        "  • `narrative`: at least 3 paragraphs (target 4), ≥400 characters, paragraphs separated by a single blank line (\\n\\n). "
        "It MUST explain the BLEND — how primary and secondary interact, what that combination looks like day-to-day, strengths and growth edges — "
        "with at least one concrete reference to an answer the user gave. Plain paragraphs only: no bullet lists, headings, or markdown.\n"
        "  • `title`: name the blend, e.g. \"You're a D/C blend — the steady driver\". Keep it specific to the primary+secondary.\n\n"
        "Return ONLY this JSON object (no extra text, no code fences):\n"
        "{{\n"
        '  "title": string,\n'
        '  "dimensions": [{{ "name": string, "emphasis": number, "blurb": string }}],\n'
        '  "primary": string,\n'
        '  "secondary": string | null,\n'
        '  "narrative": string\n'
        "}}\n"
    ),

    # --- Image helper ---------------------------------------------------------
    "image_prompt_enhancer": (
        "You are an expert prompt engineer for text-to-image models.",
        "Expand this concept into a vivid, single-line prompt (comma-separated descriptors). Style: '{style}'.\n"
        "Concept: {concept}"
    ),

    # --- Analysis / Failures -------------------------------------------------
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
