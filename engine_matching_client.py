"""HTTP client helpers for the engine matching Flask API."""
from __future__ import annotations

import os
from typing import Any, Iterable

import requests

# DEFAULT_BASE_URL = os.getenv("ENGINE_MATCHING_API_URL", "http://127.0.0.1:5050")
DEFAULT_BASE_URL = os.getenv("ENGINE_MATCHING_API_URL", "https://chatbotenginematching-production.up.railway.app/")


class EngineMatchingClient:
    """Small wrapper for calling the engine matching Flask API."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = requests.post(url, json=payload, timeout=self.timeout)
        if not response.ok:
            print(f"[EngineMatchingClient] {response.status_code} error from {url}")
            print(f"[EngineMatchingClient] payload: {payload}")
            print(f"[EngineMatchingClient] response body: {response.text}")
        response.raise_for_status()
        return response.json()

    def detect_escalation(self, question: str) -> dict[str, Any]:
        print("Hello from detect_escalation: ", self._post("/detect-escalation", {"question": question}))
        return self._post("/detect-escalation", {"question": question})

    def detect_emotion(self, text: str, provider: str = "primary") -> dict[str, Any]:
        return self._post("/detect-emotion", {"text": text, "provider": provider})

    def engine_match(
        self,
        question: str,
        provider: str = "primary",
        conversation_summary: str = "",
        iphone_stock_json: str = "",
        knowledge_path: str | None = None,
        knowledge_sheet: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question": question,
            "provider": provider,
            "conversation_summary": conversation_summary,
            "iphone_stock_json": iphone_stock_json,
        }
        if knowledge_path:
            payload["knowledge_path"] = knowledge_path
        if knowledge_sheet:
            payload["knowledge_sheet"] = knowledge_sheet
        return self._post("/engine-match", payload)

    def summarize_conversation(
        self,
        provider: str = "primary",
        previous_summary: str = "",
        question: str = "",
        answer: str = "",
    ) -> dict[str, Any]:
        return self._post(
            "/summarize",
            {
                "question": question,
                "answer": answer,
                "provider": provider,
                "previous_summary": previous_summary
            },
        )

    def history_reply(
        self,
        conversation_history: Iterable[str],
        question: str,
        provider: str = "primary",
    ) -> dict[str, Any]:
        return self._post(
            "/history-reply",
            {
                "conversation_history": list(conversation_history),
                "question": question,
                "provider": provider,
            },
        )

    def history_reply_by_keyword(
        self,
        conversation_history: Iterable[str],
        question: str,
    ) -> dict[str, Any]:
        return self._post(
            "/history-reply-keyword",
            {
                "conversation_history": list(conversation_history),
                "question": question,
            },
        )

    def product_prompt(self, user_message: str, iphone_stock_json: str = "") -> dict[str, Any]:
        return self._post(
            "/product-prompt",
            {
                "user_message": user_message,
                "iphone_stock_json": iphone_stock_json,
            },
        )

    def sales_redirect(
        self,
        user_message: str,
        provider: str = "primary",
        product_json: str = "",
    ) -> dict[str, Any]:
        return self._post(
            "/sales-redirect",
            {
                "user_message": user_message,
                "provider": provider,
                "product_json": product_json,
            },
        )

    def get_recommendations(self, question: str, conversation_summary: str = "") -> str:
        result = self._post(
            "/recommend",
            {"question": question, "conversation_summary": conversation_summary},
        )
        return result.get("answer", "Product recommendation service is currently unavailable.")
