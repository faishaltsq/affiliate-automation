"""src/persona/__init__.py — canonical persona package."""
from src.persona.profile import (
    PERSONA_SEED,
    PERSONA_VERSION,
    SOL_CONVERSATIONAL_SEED,
    SOL_CONVERSATIONAL_VERSION,
    activate_persona_version,
    get_active_persona,
    get_persona_version,
    get_sol_persona,
    list_persona_versions,
    persona_prompt_block,
    save_persona,
    voice_variation_block,
)
from src.persona.reference_library import (
    NEGATIVE_EXAMPLES,
    POSITIVE_EXAMPLES,
    REFERENCE_LIBRARY_VERSION,
    ReferenceExample,
    get_editorial_reference_prompt,
    get_reference_examples,
)
from src.persona.voice import (
    FORMATS,
    GOALS,
    LENGTHS,
    MOODS,
    OPENINGS,
    SINGLE_FORMATS,
    THREAD_FORMATS,
    VoiceVariation,
    decide_format,
    random_variation,
    should_allow_thread,
    varied_variation,
)
