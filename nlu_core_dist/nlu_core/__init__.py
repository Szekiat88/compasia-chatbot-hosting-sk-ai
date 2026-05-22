"""
nlu_core — Natural Language Understanding core library.

Multilingual intent classification, escalation detection,
and conversation management for customer support chatbots.
"""
from __future__ import annotations

import os
import sys

# Ensure the compiled extension bundled with this package is importable
sys.path.insert(0, os.path.dirname(__file__))

from engine_core import (  # noqa: F401  (compiled extension)
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OPENAI_MODEL,
    FALLBACK_GEMINI_MODEL,
    LOG_TICKET,
    MATCH_GEMINI_MODEL,
    build_product_enquiry_prompt,
    detect_escalation,
    engine_match,
    find_relevant_history_reply,
    summarize_conversation,
)

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "FALLBACK_GEMINI_MODEL",
    "LOG_TICKET",
    "MATCH_GEMINI_MODEL",
    "build_product_enquiry_prompt",
    "detect_escalation",
    "engine_match",
    "find_relevant_history_reply",
    "summarize_conversation",
]
