from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

from _params import _T

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

_KEY_B = os.getenv("OPENAI_API_KEY")
_ENGINE_C = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LOG_TICKET = "log_ticket"

EMBED_MODEL = "all-MiniLM-L6-v2"

# Public aliases expected by nlu_core (kept for backwards compatibility)
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
DEFAULT_OPENAI_MODEL = _ENGINE_C
FALLBACK_GEMINI_MODEL = os.getenv("FALLBACK_GEMINI_MODEL", "models/gemini-2.0-flash")
MATCH_GEMINI_MODEL = os.getenv("MATCH_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

_embed_model_instance = None
_embed_lock = threading.Lock()


def _get_embed_model():
    global _embed_model_instance
    with _embed_lock:
        if _embed_model_instance is None:
            from sentence_transformers import SentenceTransformer
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            _embed_model_instance = SentenceTransformer(EMBED_MODEL, device="cpu")
        return _embed_model_instance


def _get_alt_client() -> OpenAI:
    if not _KEY_B:
        raise RuntimeError("Missing required API key. Check your .env file.")
    return OpenAI(api_key=_KEY_B)


def _build_default_stock_schema() -> str:
    return (
        "CREATE TABLE shopify_variant_new (\n"
        "  product_id  BIGINT NOT NULL,\n"
        "  variant_id  BIGINT NOT NULL,\n"
        "  color       TEXT,\n"
        "  spec        TEXT,\n"
        "  condition   TEXT,\n"
        "  price       NUMERIC(12,2),\n"
        "  handle      TEXT,\n"
        "  vendor      TEXT,\n"
        "  product_type TEXT,\n"
        "  tenure      TEXT\n"
        ");"
    )


def _try_parse_schema(stock_table_schema: str) -> str | None:
    if not stock_table_schema:
        return None
    try:
        parsed = json.loads(stock_table_schema)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and {"columns", "table"}.issubset(parsed.keys()):
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    return None


def _build_spec(user_question: str, options: Iterable[str], conversation_summary: str = "") -> str:
    summary_section = ""
    if conversation_summary:
        summary_section = (
            "\nPrevious conversation summary:\n"
            f"\"\"\"\n{conversation_summary}\n\"\"\"\n\n"
            "Use this summary to maintain context. If unrelated, ignore it.\n"
        )
    return (
        _T[0]
        .replace("{summary_section}", summary_section)
        .replace("{options}", str(list(options)))
        .replace("{user_question}", user_question)
    )


def _build_product_spec(
    user_message: str,
    stock_table_schema: str,
    conversation_summary: str = "",
) -> str:
    summary_section = ""
    if conversation_summary:
        summary_section = (
            "\nConversation summary:\n"
            f"\"\"\"\n{conversation_summary}\n\"\"\"\n\n"
            "Use the summary to avoid repeating prior proposals and focus on unmet needs.\n"
        )

    schema = stock_table_schema.strip()
    _schema_parsed = _try_parse_schema(schema)
    if not _schema_parsed and not schema:
        schema = _build_default_stock_schema()

    _sys = _T[1].replace("{summary_section}", summary_section)
    if _schema_parsed:
        schema_block = f"Schema (JSON):\n{_schema_parsed}\n"
    else:
        schema_block = f"Database schema (DDL):\n{schema}\n"

    return f"{_sys}\n{schema_block}\nUser message:\n{user_message}\n\nGenerate the SQL query now."


build_product_enquiry_prompt = _build_product_spec


def detect_escalation(user_question: str) -> Tuple[bool, str]:
    text = user_question.lower()

    greeting_keywords = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "good day"]
    ticket_keywords   = ["ticket", "support ticket", "log a ticket", "open a ticket", "raise a ticket", "submit a ticket"]
    human_keywords    = ["human", "agent", "representative", "support person", "staff",
                         "talk to", "speak to", "live agent", "real person"]
    ticket_logged_kw  = ["ticket logged", "ticket was logged", "ticket has been logged",
                         "already logged a ticket", "already raised a ticket", "already opened a ticket",
                         "already contacted customer service", "already contacted support",
                         "already spoke to agent", "already talked to agent", "already went to cs", "went to cs"]

    if any(text == t or text.startswith(f"{t} ") for t in greeting_keywords):
        return True, "How can I help you today?"
    if any(t in text for t in ticket_logged_kw):
        return True, "TICKET_LOGGED"
    if any(t in text for t in human_keywords):
        return True, LOG_TICKET
    if any(t in text for t in ticket_keywords):
        return True, LOG_TICKET

    return False, ""


_INTENT_MATCH_THRESHOLD = 0.30


def engine_match(
    user_question: str,
    knowledge_df,
    provider: str = "local",
    conversation_summary: str = "",
    stock_table_schema: str = "",
) -> Tuple[str, float, object | None]:
    keyword_series = knowledge_df["keyword"].astype(str).str.strip()
    options = keyword_series.tolist()

    model = _get_embed_model()
    q_vec = model.encode([user_question], convert_to_numpy=True, normalize_embeddings=True)[0]
    opt_vecs = model.encode(options, convert_to_numpy=True, normalize_embeddings=True)
    scores = np.dot(opt_vecs, q_vec)
    best_idx = int(np.argmax(scores))
    score = float(scores[best_idx])

    if score < _INTENT_MATCH_THRESHOLD:
        return "NO_MATCH", 0.0, None

    match = options[best_idx]
    upper = match.strip().upper()
    if upper == "TICKET_LOGGED":
        match = "TICKET_LOGGED"
    elif upper in {"LOGGING_TICKET", "LOG_TICKET"}:
        match = LOG_TICKET

    if match == "PRODUCT_ENQUIRE":
        return match, score, []

    matched_rows = knowledge_df[keyword_series.str.lower() == match.lower()]
    matched_row = matched_rows.iloc[0] if not matched_rows.empty else None
    return match, score, matched_row


def find_relevant_history_reply(
    conversation_history: Iterable[str],
    current_question: str,
    provider: str = "local",
) -> str | None:
    cleaned = [str(m).strip() for m in conversation_history if str(m).strip()]
    trimmed = current_question.strip()
    if cleaned and trimmed and cleaned[-1].lower() == trimmed.lower():
        cleaned = cleaned[:-1]
    if not cleaned or not trimmed:
        return None

    model = _get_embed_model()
    q_vec = model.encode([trimmed], convert_to_numpy=True, normalize_embeddings=True)[0]
    h_vecs = model.encode(cleaned, convert_to_numpy=True, normalize_embeddings=True)
    sim_scores = np.dot(h_vecs, q_vec)
    best_idx = int(np.argmax(sim_scores))
    if float(sim_scores[best_idx]) < 0.5:
        return None
    return cleaned[best_idx]


def summarize_conversation(
    conversation_history: Iterable[str],
    provider: str = "local",
    previous_summary: str = "",
) -> str:
    history_lines = [str(m).strip() for m in conversation_history if str(m).strip()]
    if not history_lines:
        return previous_summary.strip()
    recent = history_lines[-6:]
    summary = "\n".join(recent)
    if previous_summary.strip():
        return f"{previous_summary.strip()}\n{summary}"
    return summary
