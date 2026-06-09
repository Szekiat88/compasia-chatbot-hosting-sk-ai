from __future__ import annotations

import os
import re
import json
import difflib
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Dict, Optional, Tuple
from threading import Lock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("services")

import pandas as pd
import psycopg2
from dotenv import load_dotenv
import random
from google import genai as _gapi


from openai import OpenAI as _openai_lib
from _params import _T

from nlu_core import detect_escalation, engine_match, summarize_conversation
from _ai_config import (
    get_primary_key,
    get_translation_key,
    PRIMARY_MODEL,
    TRANSLATION_MODEL,
    PROVIDER_TRANSLATION,
)
from excel_utils import (
    append_rows_to_sheet,
    load_knowledge_base,
    load_questions_excel,
    save_dataframe_to_excel,
)
from local_engine import LocalEngineClient as EngineMatchingClient, _get_knowledge_df, _run_store_locator
from faq_handler import is_faq_query, run_faq_lookup
from ai_pipeline import (
    SentimentAnalyzer,
    ProductRecommender,
    ResponseGenerator,
    ConversationMemory,
    RAGKnowledgeBase,
)
from marketplace_tracker import (
    extract_medusa_order_id,
    extract_cam_order_id,
    get_marketplace_order,
    format_marketplace_order,
    get_marketplace_order_detail,
)
from shopify_tracker import get_order_detail
from zoho_ticket_creation import (
    DEPT_GENERAL,
    PRIORITY_MEDIUM,
    create_zoho_ticket,
)
from conversation_ids import conversation_no, customer_no, message_no

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
# ------------------------------------------------------
# CONFIG
# ------------------------------------------------------

KNOWLEDGE_EXCEL_PATH = BASE_DIR / "data" / "Samples.xlsx"
KNOWLEDGE_SHEET_NAME = "Main DB"
USER_QUESTIONS_SHEET_NAME = "User Questions"

QUESTIONS_EXCEL_PATH = BASE_DIR / "data" / "chatbot_question.xlsx"
QUESTIONS_SHEET_NAME = "Random Sample"

responses = [
    "Sorry for the delay. Your request has been noted and is currently under review. We’ll update you soon.",
    "Apologies for the wait. We’ve received your request and our team is checking it now.",
    "Thanks for your patience. Your request is already logged and being reviewed by our team.",
    "Sorry for the delay. We’re currently checking on this and will get back to you shortly.",
    "We’ve noted your request and are looking into it. Thanks for your patience.",
    "Apologies for the wait. This is already being reviewed and we’ll update you once ready.",
    "Your request has been received and is under review. We’ll keep you updated."
]
_sentiment_analyzer     = SentimentAnalyzer()
_product_recommender    = ProductRecommender()
_response_generator     = ResponseGenerator()
_conversation_memory    = ConversationMemory()
_rag_kb                 = RAGKnowledgeBase()
engine_matching_client  = EngineMatchingClient()  # retained for detect_emotion
_knowledge_rows_cache: list[dict] | None = None
_knowledge_rows_cache_lock = Lock()
_proc_client = None
_proc_lock = Lock()
_CORE = PRIMARY_MODEL


# ------------------------------------------------------
# Database helpers (chat history)
# ------------------------------------------------------
def load_db_env() -> dict[str, str]:
    env_path = BASE_DIR / "db.env"
    if not env_path.exists():
        return {}
    data: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def get_db_config() -> dict[str, str]:
    env = load_db_env()
    return {
        "host": os.getenv("DB_HOST") or env.get("DB_HOST", ""),
        "port": os.getenv("DB_PORT") or env.get("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME") or env.get("DB_NAME", ""),
        "user": os.getenv("DB_USER") or env.get("DB_USER", ""),
        "password": os.getenv("DB_PASSWORD") or env.get("DB_PASSWORD", ""),
    }


def with_db_connection():
    config = get_db_config()
    missing = [key for key, value in config.items() if not value]
    if missing:
        print(f"Missing DB settings: {', '.join(missing)}")
        return None
    try:
        return psycopg2.connect(**config)
    except Exception as exc:
        print(f"Database connection failed: {exc}")
        return None


def ensure_customer_record(
    name: str | None = None,
    phone: str | None = None,
) -> tuple[str, str] | None:
    conn = with_db_connection()
    if not conn:
        return None
    try:
        clean_name = name.strip() if name else None
        clean_phone = phone.strip() if phone else None
        with conn:
            with conn.cursor() as cur:
                if clean_phone:
                    cur.execute(
                        "SELECT cust_id, cust_no FROM customer WHERE cust_phone = %s ORDER BY created_at DESC LIMIT 1",
                        (clean_phone,),
                    )
                    row = cur.fetchone()
                    if row:
                        return str(row[0]), str(row[1])
                new_customer_no = customer_no()
                cur.execute(
                    "INSERT INTO customer (cust_name, cust_phone, cust_no) VALUES (%s, %s, %s) RETURNING cust_id",
                    (clean_name, clean_phone, new_customer_no),
                )
                return str(cur.fetchone()[0]), new_customer_no
    finally:
        conn.close()


def create_conversation_record(customer_id: str | None, customer_no_value: str | None) -> str | None:
    conn = with_db_connection()
    if not conn:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    (
                        "INSERT INTO chat_conversation (cust_id, conv_no, cust_no, conv_summary) "
                        "VALUES (%s, %s, %s, %s) RETURNING conv_id"
                    ),
                    (customer_id, conversation_no(), customer_no_value, None),
                )
                return str(cur.fetchone()[0])
    finally:
        conn.close()


def _ensure_error_text_column() -> None:
    conn = with_db_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    ALTER TABLE chat_message_log
                    ADD COLUMN IF NOT EXISTS error_text TEXT
                    """
                )
    finally:
        conn.close()


_error_col_ready = False


def store_message_record(
    conversation_id: str,
    question: str,
    answer: str,
    processing_time_ms: int | None = None,
) -> None:
    conn = with_db_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT conv_no FROM chat_conversation WHERE conv_id = %s",
                    (conversation_id,),
                )
                row = cur.fetchone()
                conv_no = str(row[0]) if row else None
                cur.execute(
                    (
                        "INSERT INTO chat_message_log "
                        "(conv_id, message_question, message_answer, message_no, conv_no, processing_time_ms) "
                        "VALUES (%s, %s, %s, %s, %s, %s)"
                    ),
                    (conversation_id, question, answer, message_no(), conv_no, processing_time_ms),
                )
    finally:
        conn.close()


def store_error_record(conversation_id: str | None, question: str, error_text: str) -> None:
    global _error_col_ready
    if not _error_col_ready:
        _ensure_error_text_column()
        _error_col_ready = True
    conn = with_db_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                conv_no = None
                if conversation_id:
                    cur.execute(
                        "SELECT conv_no FROM chat_conversation WHERE conv_id = %s",
                        (conversation_id,),
                    )
                    row = cur.fetchone()
                    conv_no = str(row[0]) if row else None
                cur.execute(
                    (
                        "INSERT INTO chat_message_log "
                        "(conv_id, message_question, message_answer, message_no, conv_no, error_text) "
                        "VALUES (%s, %s, %s, %s, %s, %s)"
                    ),
                    (conversation_id, question, None, message_no(), conv_no, error_text),
                )
    finally:
        conn.close()


def update_conversation_summary(conversation_id: str, summary: str | None) -> None:
    if not summary:
        return
    conn = with_db_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE chat_conversation SET conv_summary = %s WHERE conv_id = %s",
                    (summary, conversation_id),
                )
    finally:
        conn.close()


# ------------------------------------------------------
# Emotion detection helpers
# ------------------------------------------------------
def _consoling_prefix(emotion: str) -> str:
    """Provide a brief consoling sentence tailored to the detected emotion."""

    messages = {
        "frustrated": "I’m sorry this has been frustrating. Let me help sort it out.",
        "worried": "I hear your concern and will do my best to help.",
        "confused": "I know this can be confusing—I'll clarify it for you.",
        "sad": "I’m sorry this has been disappointing. I’ll do what I can to help.",
    }

    return messages.get(emotion, "")


SHORT_ANSWER_THRESHOLD = 400


def _rephrase_as_human(raw_answer: str, user_question: str) -> str:
    client = _get_proc_client()
    if client is None:
        return raw_answer

    _spec = _T[15].replace("{user_question}", user_question).replace("{raw_answer}", raw_answer)
    try:
        response = client.models.generate_content(model=_CORE, contents=_spec)
        rephrased = (response.text or "").strip()
        return rephrased or raw_answer
    except Exception as exc:
        print(f"Rephrase failed: {exc}")
        return raw_answer


def _prepend_consoling_message(answer: str, user_input: str, provider: str) -> str:
    """Prepend a consoling sentence when user emotion is detected."""

    emotion = _sentiment_analyzer.analyze_emotion(user_input, provider=provider)
    prefix = _consoling_prefix(emotion)
    if prefix:
        return f"{prefix} {answer}"
    return answer


def _get_proc_client():
    global _proc_client
    with _proc_lock:
        if _proc_client is not None:
            return _proc_client
        api_key = get_primary_key()
        if not api_key:
            return None
        _proc_client = _gapi.Client(api_key=api_key)
        return _proc_client


def _detect_user_language_code(text: str) -> str:
    user_text = str(text or "").strip()
    if not user_text:
        return "en"

    client = _get_proc_client()
    if client is None:
        return "en"

    _spec = (
        "Detect the language of this user message. "
        "Return only a 2-letter ISO 639-1 code in lowercase (examples: en, ms, id, zh, ta). "
        "If mixed, return the dominant language.\n\n"
        f"Message: {user_text}"
    )
    try:
        response = client.models.generate_content(model=_CORE, contents=_spec)
        text_response = (response.text or "").strip().lower()
        match = re.search(r"\b[a-z]{2}\b", text_response)
        if match:
            return match.group(0)
    except Exception as exc:
        print("Language detection failed:", exc)
    return "en"


def _translate_text_to_language(text: str, target_language_code: str) -> str:
    source_text = str(text or "")
    target = str(target_language_code or "").strip().lower()
    if not source_text or not target or target == "en":
        return source_text

    client = _get_proc_client()
    if client is None:
        return source_text

    _spec = (
        "Translate the following customer support response into the target language.\n"
        f"Target language code: {target}\n"
        "Rules:\n"
        "- Preserve URLs, product names, model numbers, ticket IDs, and formatting/new lines.\n"
        "- Keep the meaning unchanged.\n"
        "- Return only the translated response text.\n\n"
        f"Response:\n{source_text}"
    )
    try:
        response = client.models.generate_content(model=_CORE, contents=_spec)
        translated = (response.text or "").strip()
        return translated or source_text
    except Exception as exc:
        print("Response translation failed:", exc)
        return source_text


# ------------------------------------------------------
# ENGINE MATCH PIPELINE
# ------------------------------------------------------
def _run_engine_match_pipeline(
    question: str,
    provider: str,
    conversation_summary: str,
) -> dict:
    """Run intent classification then fall back to store locator and FAQ when needed."""
    from store_locator import is_location_query

    # Always check store locator FIRST so queries like "nearest store in KL"
    # are never intercepted by a KB entry.
    if is_location_query(question):
        store_result = _run_store_locator(question, provider)
        match_key = "STORE_LOCATOR_NEEDS_LOCATION" if store_result["needs_location"] else "STORE_LOCATOR"
        store_reply = store_result.get("reply", "")
        return {
            "match": match_key,
            "score": 1.0,
            "matched_row": {"keyword": match_key, "answer": store_reply},
        }

    df = _get_knowledge_df()
    match, score, matched_row_raw = engine_match(
        user_question=question,
        knowledge_df=df,
        provider=provider,
        conversation_summary=conversation_summary,
    )

    if match == "STORE_LOCATOR":
        store_result = _run_store_locator(question, provider)
        match_key = "STORE_LOCATOR_NEEDS_LOCATION" if store_result["needs_location"] else "STORE_LOCATOR"
        store_reply = store_result.get("reply", "")
        return {
            "match": match_key,
            "score": score,
            "matched_row": {"keyword": match_key, "answer": store_reply},
        }

    if match != "NO_MATCH":
        payload = matched_row_raw.to_dict() if isinstance(matched_row_raw, pd.Series) else matched_row_raw
        return {"match": match, "score": score, "matched_row": payload}

    if is_faq_query(question):
        _ai = _gapi.Client(api_key=get_primary_key())
        _oa = _openai_lib(api_key=get_translation_key()) if provider.lower() == PROVIDER_TRANSLATION else None
        faq_result = run_faq_lookup(
            question, provider,
            ai_client=_ai,
            openai_client=_oa,
            ai_model=PRIMARY_MODEL,
            openai_model=TRANSLATION_MODEL,
        )
        faq_reply = faq_result.get("reply", "")
        return {
            "match": "FAQ",
            "score": 1.0,
            "matched_row": {"keyword": "FAQ", "answer": faq_reply},
        }

    return {"match": "NO_MATCH", "score": 0.0, "matched_row": None}


# ------------------------------------------------------
# FINAL SEARCH FUNCTION
# ------------------------------------------------------
def _is_greeting(text: str) -> bool:
    """Return True when the message is a simple greeting."""

    normalized = re.sub(r"\s+", " ", text).strip().lower()
    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "good day",
    }
    return normalized in greetings or normalized.startswith("hi ") or normalized.startswith("hello ")


def _is_closing_or_thanks_only(text: str) -> bool:
    """Return True for short sign-off / gratitude messages with no new request."""

    normalized = re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False

    product_signals = ("iphone", "ipad", "macbook", "price", "stock", "recommend", "buy")
    if any(signal in normalized for signal in product_signals):
        return False

    closing_terms = ("bye", "goodbye", "see you", "take care", "ok bye", "okay bye")
    thanks_terms = ("thanks", "thank you", "thx", "tq", "ty", "appreciate it")
    token_count = len(normalized.split())

    return token_count <= 8 and (
        any(term in normalized for term in closing_terms)
        or any(term in normalized for term in thanks_terms)
    )


def _extract_customer_details(text: str) -> dict[str, str] | None:
    """Extract labeled customer details from a message if present."""

    pattern = re.compile(
        r"(full\s*name|ic\s*number|order\s*id)\s*:\s*(.+?)(?=(full\s*name|ic\s*number|order\s*id)\s*:|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return None

    details: dict[str, str] = {}
    for match in matches:
        label = match.group(1).lower().strip()
        value = match.group(2).strip()
        if not value:
            continue
        if "full" in label:
            details["full_name"] = value
        elif "ic" in label:
            details["ic_number"] = value
        elif "order" in label:
            clean_value = re.split(r"\s+", value, maxsplit=1)[0]
            clean_value = re.sub(r"\s+", "", clean_value)
            details["order_id"] = clean_value

    return details or None



def _normalize_keyword_text(value: str) -> str:
    normalized = (value or "").lower()
    normalized = normalized.replace("’", "'")
    normalized = re.sub(r"^[\s\-\u2022]+", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_match_candidates(raw_match: str) -> list[str]:
    candidates: list[str] = []
    if not raw_match:
        return candidates

    for part in [raw_match, *raw_match.splitlines(), *raw_match.split("/")]:
        token = _normalize_keyword_text(part)
        if token and token not in candidates:
            candidates.append(token)
    return candidates


def _load_knowledge_rows_cached() -> list[dict]:
    global _knowledge_rows_cache
    with _knowledge_rows_cache_lock:
        if _knowledge_rows_cache is not None:
            return _knowledge_rows_cache

        knowledge_df = load_knowledge_base(KNOWLEDGE_EXCEL_PATH, KNOWLEDGE_SHEET_NAME)
        records: list[dict] = []
        for _, row in knowledge_df.iterrows():
            keyword = str(row.get("keyword") or "").strip()
            if not keyword:
                continue
            item = {col: ("" if pd.isna(val) else val) for col, val in row.to_dict().items()}
            item["keyword"] = keyword
            item["_normalized_keyword"] = _normalize_keyword_text(keyword)
            records.append(item)
        _knowledge_rows_cache = records
        return _knowledge_rows_cache


def _resolve_matched_row_fallback(raw_match: str) -> dict | None:
    try:
        candidates = _build_match_candidates(raw_match)
        if not candidates:
            return None
        rows = _load_knowledge_rows_cached()
    except Exception as exc:
        print("Knowledge fallback load failed:", exc)
        return None

    # 1) Exact normalized match against whole text/lines/slash segments.
    for candidate in candidates:
        for row in rows:
            if candidate == row.get("_normalized_keyword"):
                return row

    # 2) Containment for verbose model outputs that include bullet lines.
    for candidate in candidates:
        if len(candidate) < 6:
            continue
        for row in rows:
            norm_keyword = str(row.get("_normalized_keyword") or "")
            if candidate in norm_keyword or norm_keyword in candidate:
                return row

    # 3) Similarity fallback for small wording differences like "wheres" vs "where is".
    best_score = 0.0
    best_row: dict | None = None
    for candidate in candidates:
        if len(candidate) < 6:
            continue
        for row in rows:
            norm_keyword = str(row.get("_normalized_keyword") or "")
            if not norm_keyword:
                continue
            score = difflib.SequenceMatcher(None, candidate, norm_keyword).ratio()
            if score > best_score:
                best_score = score
                best_row = row
    if best_score >= 0.82:
        return best_row

    return None


def _format_ticket_description(
    user_question: str,
    conversation_history: str | list[str] | None,
    matched_row: dict,
) -> str:
    if isinstance(conversation_history, str):
        normalized = conversation_history.strip()
        chunks = [line.strip() for line in normalized.splitlines() if line.strip()] if normalized else []
    elif isinstance(conversation_history, list):
        chunks = [str(entry).strip() for entry in conversation_history if str(entry).strip()]
    else:
        chunks = []

    history_lines = [f"- {entry}" for entry in chunks]
    history_section = "\n".join(history_lines) if history_lines else "- (none)"
    keyword = matched_row.get("keyword", "n/a")
    answer = matched_row.get("answer", "")
    return (
        "Customer question:\n"
        f"{user_question}\n\n"
        "Matched keyword:\n"
        f"{keyword}\n\n"
        "Suggested response:\n"
        f"{answer}\n\n"
        "Conversation history:\n"
        f"{history_section}"
    )


def _create_ticket_from_match(
    user_question: str,
    conversation_history: str | list[str] | None,
    matched_row: dict,
) -> str | None:
    description = _format_ticket_description(user_question, conversation_history, matched_row)
    print("Hello description: ", description)
    subject = matched_row.get("keyword") or "Customer request"
    ticket = create_zoho_ticket(
        subject=subject,
        description=description,
        department_id=DEPT_GENERAL,
        priority=PRIORITY_MEDIUM,
    )

    return str(ticket.get("id") or ticket.get("ticketNumber")) if ticket else None


def search(
    user_question,
    engine_mode,
    conversation_summary: str = "",
    conversation_id: str | None = None,
):
    search_started_at = time.perf_counter()
    _mode = engine_mode

    def elapsed_processing_time_ms() -> int:
        return max(0, int((time.perf_counter() - search_started_at) * 1000))

    log.debug("── search() START ──────────────────────────")
    log.debug("Question      : %s", user_question)
    log.debug("Conv summary  : %s", conversation_summary[:120] if conversation_summary else "(none)")
    ticket_already_logged = _ticket_logged(conversation_summary)
    log.debug("Ticket already logged: %s", ticket_already_logged)
    log.debug("Detecting user language...")
    user_language_code = _detect_user_language_code(str(user_question))
    log.debug("Language detected: %s", user_language_code)

    def make_response(
        answer: str,
        confidence: str = "high",
        anchor=None,
        ticket_logged: bool = False,
        summary: str = None,
        raw_response: bool = False,
        translate_output: bool = True,
        processing_time_ms: int | None = None,
    ):
        empathetic_answer = (
            str(answer)
            if raw_response
            else _prepend_consoling_message(
                str(answer),
                str(user_question),
                provider=_mode,
            )
        )
        if translate_output and not raw_response:
            empathetic_answer = _translate_text_to_language(empathetic_answer, user_language_code)

        log.debug("Summarising conversation...")
        try:
            summary = _conversation_memory.summarize(
                user_question=str(user_question),
                answer=str(answer),
                provider=_mode,
                previous_summary=conversation_summary or "",
            )
            log.debug("Summary updated OK")
        except Exception as exc:
            log.error("Summarize failed: %s", exc)
            summary = conversation_summary or None
        if conversation_id:
            measured_processing_time_ms = (
                processing_time_ms
                if processing_time_ms is not None
                else elapsed_processing_time_ms()
            )
            store_message_record(
                conversation_id,
                str(user_question),
                empathetic_answer,
                measured_processing_time_ms,
            )
            if ticket_logged:
                update_conversation_summary(conversation_id, summary)

        return {
            "anchor_token": anchor,
            "answer": empathetic_answer,
            "confidence": confidence,
            "conversation_summary": summary,
            "ticket_logged": ticket_logged or ticket_already_logged,
            "processing_time_ms": (
                processing_time_ms
                if processing_time_ms is not None
                else elapsed_processing_time_ms()
            ),
        }

    # if ticket_already_logged:# and _is_greeting(str(user_question)):
    #     return make_response(
    #         "Your ticket has already been logged. Our team is working on it; please share any new details if needed.",
    #         ticket_logged=True,
    #     )

    user_text = str(user_question)

    if _is_greeting(user_text):
        log.debug("Intent: GREETING — returning default greeting")
        return make_response("How can I help you today?")

    if _is_closing_or_thanks_only(user_text):
        log.debug("Intent: CLOSING/THANKS — returning farewell")
        return make_response(
            "You're welcome. If you need anything else, just message me anytime.",
            raw_response=True,
        )

    log.debug("Extracting customer details / order ID...")
    details = _extract_customer_details(user_text)
    cam_order_id = extract_cam_order_id(user_text)
    
    if cam_order_id:
        order_id = (details or {}).get("order_id") or cam_order_id
        log.debug("Order ID found: %s", order_id)
        if order_id:
            log.info("cam_lookup_triggered order_id=%s user_text=%r", order_id, user_text)
            marketplace_order = get_marketplace_order(order_id)
            found = marketplace_order is not None
            log.info("marketplace_lookup_result order_id=%s found=%s", order_id, found)
            if not found:
                log.warning("marketplace_order_not_found order_id=%s", order_id)
                if conversation_id:
                    store_error_record(
                        conversation_id,
                        str(user_question),
                        f"Marketplace order not found: {order_id}",
                    )
            order_reply = format_marketplace_order(marketplace_order)
            result = make_response(order_reply, raw_response=True)
            result["marketplace_order_id"] = order_id
            result["marketplace_found"] = found
            return result
        return make_response(json.dumps(details), raw_response=True)

    log.debug("Checking escalation...")
    should_escalate, escalation_message = _sentiment_analyzer.analyze_escalation(user_question)
    log.debug("Escalation result: %s", should_escalate)

    if should_escalate:
        log.debug("Intent: ESCALATION — creating ticket")
        return make_response(
            "If you want to contact our live support team, please click the following link https://wa.me/60129417355?text=UAT%20Testing%20Completed",
            ticket_logged=True,
        )

    log.debug("Running RAG knowledge base retrieval...")
    match_response = _rag_kb.retrieve(
        question=user_question,
        provider=_mode,
        conversation_summary=conversation_summary,
    )
    match = match_response.get("match")
    matched_row = match_response.get("matched_row")
    log.debug("RAG retrieval result: %s", match)

    if not match or match == "NO_MATCH":
        log.debug("Intent: NO_MATCH — running sales redirect")
        redirect_reply = _response_generator.sales_redirect(user_question, provider=_mode)
        return make_response(
            redirect_reply or "Sorry, I couldn't find a related answer.",
            confidence="low",
        )
    if match == "TICKET_LOGGED":
        log.debug("Intent: TICKET_LOGGED — returning ticket response")
        return make_response(
        random.choice(responses),
        confidence="low",
    )
    if match == "PRODUCT_ENQUIRE":
        log.debug("Intent: PRODUCT_ENQUIRE — fetching recommendations")
        try:
            bot_reply = _product_recommender.recommend(
                question=str(user_question).strip(),
                conversation_summary=conversation_summary,
            )
            log.debug("Recommendations returned OK")
            return make_response(bot_reply, confidence="high", raw_response=True)
        except Exception as exc:
            log.error("Product recommendation failed: %s", exc)
            return make_response(
                "Sorry, I couldn't process your product inquiry. Please try again.",
                confidence="low",
            )

    if match in ("STORE_LOCATOR", "STORE_LOCATOR_NEEDS_LOCATION"):
        log.debug("Intent: %s — returning store reply directly", match)
        store_reply = (matched_row or {}).get("answer", "")
        return make_response(store_reply, raw_response=True)

    if matched_row is None:
        log.warning("No matched_row — trying local fallback for keyword: %s", match)
        matched_row = _resolve_matched_row_fallback(str(match or ""))
        if matched_row is None:
            log.warning("Fallback failed — no answer found for: %s", match)
            return make_response("No relevant answer found.", confidence="low")
        log.debug("Fallback recovered keyword: %s", matched_row.get("keyword"))

    answer = matched_row["answer"]
    anchor_token = matched_row["keyword"]
    log.debug("Intent: FAQ match — keyword: %s", anchor_token)

    # Long answers: rephrase via AI into a natural human reply and send as one message.
    # Short answers (≤ threshold): keep as-is so empty-row splitting can send them as separate messages.
    if len(answer) > SHORT_ANSWER_THRESHOLD:
        answer = _response_generator.rephrase(answer, user_text)
        reply_anchor = None  # skip split in WhatsApp layer
    else:
        reply_anchor = anchor_token

    action = matched_row.get("action")
    if isinstance(action, str):
        action = action.strip().lower()
    if action == "log_ticket":
        log.debug("Action: log_ticket — creating support ticket")
        return make_response(
            "If you want to contact our live support team, please click the following link https://wa.me/60129417355?text=UAT%20Testing%20Completed",
            anchor=reply_anchor,
            ticket_logged=True,
        )
    append_rows_to_sheet(
        pd.DataFrame(
            [
                {
                    "User Question": user_question,
                    "Anchor Token": anchor_token,
                    "Answer": answer,
                }
            ]
        ),
        KNOWLEDGE_EXCEL_PATH,
        USER_QUESTIONS_SHEET_NAME,
    )

    return make_response(answer, anchor=reply_anchor)


def _ticket_logged(conversation_history: str | list[str] | None) -> bool:
    """Return True when prior history already logged a ticket."""

    if not conversation_history:
        return False

    ticket_markers = ("ticket has been logged", "ticket is logged", "ticket logged")
    if isinstance(conversation_history, str):
        entries = [conversation_history]
    else:
        entries = conversation_history
    for entry in entries:
        normalized = str(entry).strip().lower()
        if any(marker in normalized for marker in ticket_markers):
            return True
    return False

# def record_answers_to_excel(engine_mode: str):
#     output_sheet_name = engine_mode + "_realtime_tested_output"
#     questions_df, question_column = load_questions_excel(
#         QUESTIONS_EXCEL_PATH, QUESTIONS_SHEET_NAME
#     )

#     anchor_tokens = []
#     answers = []
#     confidences = []

#     for question in questions_df[question_column]:
#         if pd.isna(question):
#             anchor_tokens.append("")
#             answers.append("")
#             confidences.append("")
#             continue

#         result = search(str(question), engine_mode, conversation_history=[])
#         anchor_tokens.append(result.get("anchor_token") or "")
#         answers.append(result.get("answer") or "")
#         confidences.append(result.get("confidence") or "")

#     questions_df["Anchor Token"] = anchor_tokens
#     questions_df["Answer"] = answers
#     questions_df["Confidence"] = confidences
#     questions_df["CreatedDate"] = datetime.now()
#     save_dataframe_to_excel(
#         questions_df,
#         QUESTIONS_EXCEL_PATH,
#         output_sheet_name,
#     )


