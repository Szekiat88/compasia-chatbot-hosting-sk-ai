import os

def get_primary_key() -> str:
    return os.getenv("GEMINI_API_KEY", "")

def get_translation_key() -> str:
    return os.getenv("OPENAI_API_KEY", "")

PROVIDER_PRIMARY     = "gemini"
PROVIDER_TRANSLATION = "openai"

try:
    from nlu_core import DEFAULT_GEMINI_MODEL as PRIMARY_MODEL
    from nlu_core import DEFAULT_OPENAI_MODEL as TRANSLATION_MODEL
except Exception:
    PRIMARY_MODEL     = os.getenv("LANGUAGE_MODEL", "models/gemini-2.5-flash")
    TRANSLATION_MODEL = "gpt-4o-mini"
