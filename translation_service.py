"""Lightweight Malay-to-English translation helper."""

from __future__ import annotations

import os
import re
from html import unescape
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from _params import _T

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

client = OpenAI()

TRANSLATION_MODEL = os.getenv("TRANSLATION_MODEL", "gpt-4o-mini")
_SYS = _T[16]


def translate_malay_to_english(text: str) -> str:
    """Translate Malay or mixed-Malay text to English, returning the original on failure."""

    normalized = sanitize_text(text)
    if not normalized:
        return ""

    try:
        response = client.chat.completions.create(
            model=TRANSLATION_MODEL,
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": normalized},
            ],
            temperature=0.2,
            max_tokens=200,
        )
    except Exception:
        return normalized

    choice = response.choices[0].message
    if not choice or not choice.content:
        return normalized

    return choice.content.strip()


def sanitize_text(raw_text: str) -> str:
    """Remove HTML markup and normalize whitespace before translation."""

    unescaped = unescape(raw_text or "")
    no_tags = re.sub(r"<[^>]+>", " ", unescaped)
    collapsed = re.sub(r"\s+", " ", no_tags)
    return collapsed.strip()


def is_english_text(raw_text: str, *, threshold: float = 0.85) -> bool:
    """Heuristically determine if text is English to avoid unnecessary translation.

    The check counts how many alphabetic characters are ASCII and compares the ratio
    to a configurable threshold. If there are no alphabetic characters, the text is
    considered English to keep behavior predictable.
    """

    sanitized = sanitize_text(raw_text)
    if not sanitized:
        return True

    alphabetic = [ch for ch in sanitized if ch.isalpha()]
    if not alphabetic:
        return True

    ascii_letters = [ch for ch in alphabetic if ch.isascii()]
    return (len(ascii_letters) / len(alphabetic)) >= threshold
