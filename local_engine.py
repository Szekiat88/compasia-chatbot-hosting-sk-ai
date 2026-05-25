"""
Local engine — same interface as EngineMatchingClient but runs everything
in-process. No Railway, no HTTP calls.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI as _openai_lib
from _ai_config import (
    get_primary_key,
    get_translation_key,
    PRIMARY_MODEL,
    TRANSLATION_MODEL,
    PROVIDER_PRIMARY,
    PROVIDER_TRANSLATION,
)

from ml_intent_engine import predict_intent as _engine_match_fn
from nlu_core import (
    detect_escalation as _detect_escalation_fn,
    find_relevant_history_reply as _history_reply_fn,
    summarize_conversation as _summarize_fn,
    build_product_enquiry_prompt as _product_prompt_fn,
)
from faq_handler import is_faq_query, run_faq_lookup
from _params import _T
from store_locator import (
    _build_store_spec,
    detect_language,
    detect_location,
    find_matching_stores,
    is_location_query,
    load_stores,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

KNOWLEDGE_EXCEL_PATH  = BASE_DIR / "data" / "Samples.xlsx"
KNOWLEDGE_SHEET_NAME  = "Main DB"

# ---------------------------------------------------------------------------
# Knowledge-base cache
# ---------------------------------------------------------------------------
_knowledge_df: pd.DataFrame | None = None
_knowledge_lock = threading.Lock()


def _get_knowledge_df() -> pd.DataFrame:
    global _knowledge_df
    with _knowledge_lock:
        if _knowledge_df is None:
            _knowledge_df = pd.read_excel(KNOWLEDGE_EXCEL_PATH, sheet_name=KNOWLEDGE_SHEET_NAME)
    return _knowledge_df


# ---------------------------------------------------------------------------
# FAISS recommendation runtime (lazy-loaded)
# ---------------------------------------------------------------------------
_rec_model = None
_rec_cache: dict | None = None
_rec_lock = threading.Lock()

CACHE_DIR = str(BASE_DIR / ".cache_semantic_search")


def _get_rec_runtime():
    global _rec_model, _rec_cache
    with _rec_lock:
        if _rec_model is None:
            from sentence_transformers import SentenceTransformer
            from semantic_search import EMBED_MODEL
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            _rec_model = SentenceTransformer(EMBED_MODEL, device="cpu")
        if not _rec_cache:
            from semantic_search import load_cache
            _rec_cache = load_cache(CACHE_DIR)
    return _rec_model, _rec_cache


# ---------------------------------------------------------------------------
# Helpers (inlined from engine_matching_flask_api.py)
# ---------------------------------------------------------------------------
_EMOTION_KEYWORDS: dict[str, list[str]] = {
    "frustrated": ["frustrated", "frustrating", "angry", "annoyed", "upset", "furious", "ridiculous", "unacceptable"],
    "worried":    ["worried", "concern", "anxious", "afraid", "scared", "nervous", "panic"],
    "confused":   ["confused", "unclear", "don't understand", "dont understand", "not sure", "what do you mean", "confusing"],
    "sad":        ["sad", "disappointed", "unhappy", "unfortunate", "terrible", "horrible", "awful"],
}


def _detect_emotion(text: str, provider: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip().lower()
    if not cleaned:
        return ""
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        if any(kw in cleaned for kw in keywords):
            return emotion
    return ""


def _history_reply_by_keyword(conversation_history: list[str], current_question: str) -> str | None:
    trimmed = current_question.strip().lower()
    history = [e for e in conversation_history if str(e).strip()]
    if history and trimmed and history[-1].strip().lower() == trimmed:
        history = history[:-1]
    if not history or not trimmed:
        return None

    def _kws(t): return {tok for tok in re.findall(r"[A-Za-z0-9']+", t.lower()) if len(tok) > 2}
    keywords = _kws(current_question)
    best, best_score = None, 0
    for entry in reversed(history):
        score = len(keywords & _kws(entry))
        if score > best_score:
            best, best_score = entry.strip(), score
    return best if best_score > 0 else None


def _build_redirect_context(user_message: str, product_json: str) -> str:
    return _T[12].replace("{user_message}", user_message).replace("{product_json}", product_json)


def _generate_response(context: str, provider: str) -> str:
    return ""


def _run_store_locator(question: str, provider: str) -> dict:
    language = detect_language(question)
    location_term = detect_location(question)

    if location_term is None:
        reply = (
            "Untuk cari kedai berdekatan, boleh beritahu kawasan atau bandar anda?"
            if language == "ms"
            else "Sure! To find the nearest store, could you share your current area or city?"
        )
        return {"needs_location": True, "stores": [], "closed_stores": [], "reply": reply, "language": language}

    active, closed = find_matching_stores(location_term, load_stores())
    if not active and not closed:
        reply = (
            "Maaf, tiada kedai ditemui berhampiran kawasan tersebut."
            if language == "ms"
            else "Sorry, no stores found near that area. Try Kuala Lumpur, Selangor, Penang, or Johor."
        )
        return {"needs_location": False, "stores": [], "closed_stores": [], "reply": reply, "language": language, "location_detected": location_term}

    _spec = _build_store_spec(question, active, language)
    reply = _generate_response(_spec, provider)
    if not reply:
        reply = "\n\n".join(
            f"📍 {s['name']}\n🏢 {s['location']}\n🕐 {s['operatingHours']}\n💬 {s['whatsappLink']}"
            for s in active[:5]
        )

    return {"needs_location": False, "stores": active, "closed_stores": closed, "reply": reply,
            "language": language, "location_detected": location_term}


# ---------------------------------------------------------------------------
# Public API — same method signatures as EngineMatchingClient
# ---------------------------------------------------------------------------
class LocalEngineClient:
    """Drop-in replacement for EngineMatchingClient. All logic runs locally."""

    def detect_escalation(self, question: str) -> dict[str, Any]:
        should_escalate, response = _detect_escalation_fn(question)
        return {"escalate": should_escalate, "response": response}

    def detect_emotion(self, text: str, provider: str = PROVIDER_PRIMARY) -> dict[str, Any]:
        return {"emotion": _detect_emotion(text, provider)}

    def engine_match(
        self,
        question: str,
        provider: str = PROVIDER_PRIMARY,
        conversation_summary: str = "",
        iphone_stock_json: str = "",
        knowledge_path: str | None = None,
        knowledge_sheet: str | None = None,
    ) -> dict[str, Any]:
        # 1. KB matching FIRST — most questions belong here
        if knowledge_path:
            sheet = knowledge_sheet or KNOWLEDGE_SHEET_NAME
            df = pd.read_excel(knowledge_path, sheet_name=sheet)
        else:
            df = _get_knowledge_df()

        match, score, matched_row = _engine_match_fn(
            question, df,
            provider=provider,
            conversation_summary=conversation_summary,
            stock_table_schema=iphone_stock_json,
        )

        if isinstance(matched_row, pd.Series):
            matched_payload: Any = matched_row.to_dict()
        else:
            matched_payload = matched_row

        if match != "NO_MATCH":
            return {"match": match, "score": score, "matched_row": matched_payload}

        # 2. Store locator fallback — only if KB has no answer
        if is_location_query(question):
            store_result = _run_store_locator(question, provider)
            match_key = "STORE_LOCATOR_NEEDS_LOCATION" if store_result["needs_location"] else "STORE_LOCATOR"
            store_reply = store_result.get("reply", "")
            return {
                "match": match_key,
                "score": 1.0,
                "matched_row": {"keyword": match_key, "answer": store_reply},
                "reply": store_reply,
                "store_locator": store_result,
            }

        # 3. FAQ fallback — only if KB and store locator both have no answer
        if is_faq_query(question):
            translate_client = _openai_lib(api_key=get_translation_key()) if provider.lower() == PROVIDER_TRANSLATION else None
            faq_result = run_faq_lookup(
                question, provider,
                ai_client=None,
                openai_client=translate_client,
                ai_model=PRIMARY_MODEL,
                openai_model=TRANSLATION_MODEL,
            )
            faq_reply = faq_result.get("reply", "")
            return {
                "match": "FAQ",
                "score": 1.0,
                "matched_row": {"keyword": "FAQ", "answer": faq_reply},
                "reply": faq_reply,
                "faq": faq_result,
            }

        return {"match": "NO_MATCH", "score": 0.0, "matched_row": None}

    def summarize_conversation(
        self,
        provider: str = PROVIDER_PRIMARY,
        previous_summary: str = "",
        question: str = "",
        answer: str = "",
    ) -> dict[str, Any]:
        history: list[str] = []
        if question.strip():
            history.append(f"Customer: {question.strip()}")
        if answer.strip():
            history.append(f"Agent: {answer.strip()}")
        summary = _summarize_fn(history, provider=provider, previous_summary=previous_summary)
        return {"summary": summary}

    def history_reply(
        self,
        conversation_history: Iterable[str],
        question: str,
        provider: str = PROVIDER_PRIMARY,
    ) -> dict[str, Any]:
        reply = _history_reply_fn(list(conversation_history), question, provider=provider)
        return {"reply": reply}

    def history_reply_by_keyword(
        self,
        conversation_history: Iterable[str],
        question: str,
    ) -> dict[str, Any]:
        reply = _history_reply_by_keyword(list(conversation_history), question)
        return {"reply": reply}

    def product_prompt(self, user_message: str, iphone_stock_json: str = "") -> dict[str, Any]:
        _spec = _product_prompt_fn(user_message, iphone_stock_json)
        return {"prompt": _spec}

    def sales_redirect(
        self,
        user_message: str,
        provider: str = PROVIDER_PRIMARY,
        product_json: str = "",
    ) -> dict[str, Any]:
        context = _build_redirect_context(user_message, product_json)
        reply = _generate_response(context, provider)
        return {"reply": reply}

    def get_recommendations(self, question: str, conversation_summary: str = "") -> str:
        try:
            from semantic_search import EMBED_MODEL, build_search_query, search_index
            from recommendation_bot import (
                _build_human_reply,
                _filter_rows_to_models_mentioned_in_reply,
                _filter_rows_to_recommended_model,
                _format_cards,
                _recommended_in_results,
                build_diverse_model_rows,
            )

            TOP_K = 3
            CANDIDATE_K = 50

            model, cache = _get_rec_runtime()
            meta = cache.get("meta", {})
            if not meta or meta.get("model") != EMBED_MODEL:
                return "Product recommendation index not ready. Run build_vectors.py first."

            index  = cache["index"]
            id_map = cache["id_map"]
            record_map = cache.get("record_map", {})

            enhanced_query = f"{question}\n\nContext: {conversation_summary}" if conversation_summary else question
            search_query, recommended_model, price_min, price_max = build_search_query(enhanced_query, None)
            effective_query = f"{recommended_model} {search_query}".strip() if recommended_model else search_query

            scores, idx, _ = search_index(model, index, effective_query, CANDIDATE_K)

            hits = [
                (rank + 1, id_map[i], float(scores[rank]))
                for rank, i in enumerate(idx)
                if 0 <= i < len(id_map)
            ]
            if not hits:
                return "No matching products found."

            rows = build_diverse_model_rows(
                hits=hits,
                record_map=record_map,
                top_k=TOP_K,
                price_min=price_min,
                price_max=price_max,
            )
            if not rows:
                return "No products match your price range."

            handles = [r["handle"] for r in rows if r.get("handle")]
            in_results = _recommended_in_results(recommended_model or "", handles)
            if in_results and recommended_model:
                rows = _filter_rows_to_recommended_model(rows, recommended_model)
                handles = [r["handle"] for r in rows if r.get("handle")]
                in_results = _recommended_in_results(recommended_model or "", handles)

            bot_reply = _build_human_reply(
                query=question,
                recommended_model=recommended_model,
                recommended_in_results=in_results,
                top_rows=rows,
                memory=[],
                price_min=price_min,
                price_max=price_max,
            )
            rows = _filter_rows_to_models_mentioned_in_reply(rows, bot_reply)
            return f"{bot_reply}\n\n{_format_cards(rows)}"

        except Exception as exc:
            import traceback
            traceback.print_exc()
            return f"Product recommendation service is currently unavailable. ({exc})"
