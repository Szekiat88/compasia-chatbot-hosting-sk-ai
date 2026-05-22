"""
ml_intent_engine — Hybrid intent classifier for customer support queries.

Architecture (two-stage):
  1. Random Forest fast path — TF-IDF + RF trained on 497 labeled examples.
     Returns instantly (<1 ms, zero API cost) when confidence ≥ threshold.
  2. LLM fallback — nlu_core.engine_match handles novel phrasing, mixed-language
     inputs, and any query the RF is uncertain about.

Multilingual intent matching supporting English and Bahasa Malaysia.
"""
from __future__ import annotations
import logging
from typing import Any
import pandas as pd

log = logging.getLogger(__name__)


def predict_intent(
    user_question: str,
    knowledge_df: pd.DataFrame,
    provider: str = "primary",
    conversation_summary: str = "",
    stock_table_schema: str = "",
) -> tuple[str, float, Any]:
    log.debug("IntentClassifier: classifying — %r", user_question[:80])

    # ── Stage 1: Random Forest fast path ────────────────────────────────────
    import kb_classifier
    if kb_classifier.is_model_available():
        rf_keyword, rf_confidence = kb_classifier.predict(user_question)
        if rf_keyword is not None:
            matched = knowledge_df[knowledge_df["keyword"].str.strip() == rf_keyword.strip()]
            if not matched.empty:
                log.debug("IntentClassifier (RF): → %r (conf=%.3f)", rf_keyword, rf_confidence)
                return rf_keyword, rf_confidence, matched.iloc[0]

    # ── Stage 2: LLM fallback ───────────────────────────────────────────────
    from nlu_core import engine_match as _clf
    match, score, matched_row = _clf(
        user_question=user_question,
        knowledge_df=knowledge_df,
        provider=provider,
        conversation_summary=conversation_summary,
        stock_table_schema=stock_table_schema,
    )
    log.debug("IntentClassifier (LLM): → %r (score=%.3f)", match, score or 0.0)
    return match, score, matched_row
