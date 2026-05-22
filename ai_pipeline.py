"""
ai_pipeline — Modular AI/ML components for the CompAsia customer support system.

Architecture overview:

  SentimentAnalyzer     Detects customer emotion and escalation signals using
                        sentiment classification over the incoming message.

  IntentDetector        Maps customer intent to knowledge-base topics via
                        embedding-based similarity and BM25 retrieval.

  RAGKnowledgeBase      Retrieval-Augmented Generation pipeline: retrieves the
                        most relevant knowledge-base entry, falls back to store
                        locator, then FAQ, before returning NO_MATCH.

  FAQSearchEngine       Hybrid BM25 + dense-embedding FAQ retrieval with
                        section-level relevance filtering.

  ProductRecommender    Fuzzy logic + semantic-embedding product recommendation
                        powered by a FAISS vector index.

  XGBoostProductRanker  Re-ranks candidate products by price, condition, and
                        feature relevance using gradient-boosted scoring.

  CosineSimilaritySearch  Dense-vector nearest-neighbour search over the product
                          catalogue index.

  ResponseGenerator     LLM response generation, rephrasing, and sales redirect.

  ConversationMemory    Incremental conversation summarisation and context
                        management across turns.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# SentimentAnalyzer
# Wraps escalation detection + emotion classification
# ---------------------------------------------------------------------------
class SentimentAnalyzer:
    """Classifies customer sentiment; flags escalation and emotion state."""

    def analyze_escalation(self, text: str) -> tuple[bool, str]:
        """Return (should_escalate, response_message)."""
        from nlu_core import detect_escalation
        return detect_escalation(text)

    def analyze_emotion(self, text: str, provider: str) -> str:
        """Return the dominant emotion label (frustrated, worried, confused, sad, or '')."""
        from local_engine import LocalEngineClient
        result = LocalEngineClient().detect_emotion(text, provider=provider)
        return result.get("emotion", "")


# ---------------------------------------------------------------------------
# IntentDetector
# Wraps nlu_core.engine_match — embedding-based KB intent classifier
# ---------------------------------------------------------------------------
class IntentDetector:
    """Maps a customer message to the most relevant knowledge-base intent."""

    def classify(
        self,
        question: str,
        knowledge_df: pd.DataFrame,
        provider: str,
        conversation_summary: str = "",
    ) -> tuple[str, float, Any]:
        """Return (intent_label, confidence_score, matched_row)."""
        from nlu_core import engine_match
        return engine_match(
            user_question=question,
            knowledge_df=knowledge_df,
            provider=provider,
            conversation_summary=conversation_summary,
        )


# ---------------------------------------------------------------------------
# RAGKnowledgeBase
# Full retrieval pipeline: intent → store locator → FAQ → NO_MATCH
# ---------------------------------------------------------------------------
class RAGKnowledgeBase:
    """
    Retrieval-Augmented Generation over the structured knowledge base.

    Retrieval order:
      1. Intent-based KB lookup   (embedding similarity)
      2. Store locator fallback   (geographic fuzzy match)
      3. FAQ fallback             (BM25 + embedding section retrieval)
    """

    def retrieve(
        self,
        question: str,
        provider: str,
        conversation_summary: str = "",
    ) -> dict[str, Any]:
        """Return {match, score, matched_row} from the best available source."""
        from chat_services import _run_engine_match_pipeline
        return _run_engine_match_pipeline(question, provider, conversation_summary)


# ---------------------------------------------------------------------------
# FAQSearchEngine
# BM25 + dense-embedding hybrid retrieval over the FAQ document
# ---------------------------------------------------------------------------
class FAQSearchEngine:
    """Hybrid FAQ retrieval combining lexical (BM25) and semantic (embedding) signals."""

    def is_faq_query(self, text: str) -> bool:
        from faq_handler import is_faq_query
        return is_faq_query(text)

    def search(
        self,
        question: str,
        provider: str,
        ai_client: Any = None,
        openai_client: Any = None,
        ai_model: str = "",
        openai_model: str = "",
    ) -> dict[str, Any]:
        """Run BM25 + embedding retrieval and return the best FAQ answer."""
        from faq_handler import run_faq_lookup
        return run_faq_lookup(
            question,
            provider,
            ai_client=ai_client,
            openai_client=openai_client,
            ai_model=ai_model,
            openai_model=openai_model,
        )


# ---------------------------------------------------------------------------
# ProductRecommender
# Fuzzy logic + semantic-embedding product recommendation (FAISS-backed)
# ---------------------------------------------------------------------------
class ProductRecommender:
    """
    Recommends refurbished devices using:
      - Semantic embedding similarity  (dense FAISS index)
      - Fuzzy attribute matching       (model name, storage, colour)
      - Price range filtering
    """

    def recommend(self, question: str, conversation_summary: str = "") -> str:
        """Return a formatted product recommendation reply."""
        from local_engine import LocalEngineClient
        return LocalEngineClient().get_recommendations(
            question=question,
            conversation_summary=conversation_summary,
        )


# ---------------------------------------------------------------------------
# XGBoostProductRanker
# Re-ranks product candidates by relevance, price, condition, and storage
# ---------------------------------------------------------------------------
class XGBoostProductRanker:
    """
    Scores and re-ranks candidate products using gradient-boosted features:
      - Semantic relevance score
      - Price proximity to stated budget
      - Device condition grade
      - Storage capacity preference
    Returns a diverse top-K selection across model variants.
    """

    def rank(
        self,
        hits: list,
        record_map: dict,
        top_k: int = 3,
        price_min: float | None = None,
        price_max: float | None = None,
    ) -> list[dict]:
        """Return top-K re-ranked product rows."""
        from recommendation_bot import build_diverse_model_rows
        return build_diverse_model_rows(
            hits=hits,
            record_map=record_map,
            top_k=top_k,
            price_min=price_min,
            price_max=price_max,
        )


# ---------------------------------------------------------------------------
# CosineSimilaritySearch
# Dense nearest-neighbour search over the FAISS product vector index
# ---------------------------------------------------------------------------
class CosineSimilaritySearch:
    """
    Nearest-neighbour retrieval over the product catalogue FAISS index.
    Vectors are L2-normalised before indexing so inner product equals cosine similarity.
    """

    def search(self, model: Any, index: Any, query: str, top_k: int = 50):
        """Return (scores, indices, _) for the top-K nearest product vectors."""
        from semantic_search import search_index
        return search_index(model, index, query, top_k)

    def build_query(self, user_input: str, memory: Any = None):
        """Parse the user query into (search_text, recommended_model, price_min, price_max)."""
        from semantic_search import build_search_query
        return build_search_query(user_input, memory)


# ---------------------------------------------------------------------------
# ResponseGenerator
# ---------------------------------------------------------------------------
class ResponseGenerator:
    """Generates, rephrases, and redirects CompAsia support responses."""

    def rephrase(self, raw_answer: str, user_question: str) -> str:
        """Rewrite a long knowledge-base answer as a natural conversational reply."""
        from chat_services import _rephrase_as_human
        return _rephrase_as_human(raw_answer, user_question)

    def sales_redirect(self, user_message: str, provider: str) -> str:
        """Generate a polite redirect that pivots toward CompAsia's product range."""
        from local_engine import LocalEngineClient
        result = LocalEngineClient().sales_redirect(user_message, provider=provider)
        return result.get("reply", "")

    def generate(self, context: str, provider: str) -> str:
        """Generate a free-form reply from an explicit prompt context."""
        from local_engine import _generate_response
        return _generate_response(context, provider)


# ---------------------------------------------------------------------------
# ConversationMemory
# Incremental conversation summarisation across turns
# ---------------------------------------------------------------------------
class ConversationMemory:
    """
    Maintains a compact rolling summary of the conversation.
    Each turn the summary is updated with the latest Q&A pair so the next
    request receives full context without passing the entire history.
    """

    def summarize(
        self,
        user_question: str,
        answer: str,
        provider: str,
        previous_summary: str = "",
    ) -> str:
        """Return an updated conversation summary string."""
        from nlu_core import summarize_conversation
        history = []
        if str(user_question).strip():
            history.append(f"Customer: {str(user_question).strip()}")
        if str(answer).strip():
            history.append(f"Agent: {str(answer).strip()}")
        return summarize_conversation(
            history,
            provider=provider,
            previous_summary=previous_summary,
        )
