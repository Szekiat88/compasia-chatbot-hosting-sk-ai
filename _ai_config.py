import os

def get_primary_key() -> str:
    return os.getenv("GEMINI_API_KEY", "")

def get_translation_key() -> str:
    return os.getenv("OPENAI_API_KEY", "")

PROVIDER_PRIMARY     = "local"
PROVIDER_TRANSLATION = "openai"

PRIMARY_MODEL     = "all-MiniLM-L6-v2"
TRANSLATION_MODEL = "gpt-4o-mini"
