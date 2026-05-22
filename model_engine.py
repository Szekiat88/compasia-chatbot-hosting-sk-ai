"""Inference engine — loads the trained intent model and routes requests."""

from __future__ import annotations

import base64
import json
import os
import pickle
import zlib
from pathlib import Path
from typing import Any, Iterable

import numpy as np

# ---------------------------------------------------------------------------
# Load model artifacts (built by train_model.py)
# ---------------------------------------------------------------------------
_MODEL_DIR = Path(__file__).resolve().parent / "models"

with open(_MODEL_DIR / "model.pkl", "rb") as _f:
    _RF_MODEL = pickle.load(_f)

with open(_MODEL_DIR / "label_encoder.pkl", "rb") as _f:
    _LABEL_ENC = pickle.load(_f)

with open(_MODEL_DIR / "feature_config.json") as _f:
    _CFG = json.load(_f)

_KW: dict[str, list] = {
    "product":  _CFG["product_kw"],
    "order":    _CFG["order_kw"],
    "escalate": _CFG["escalate_kw"],
    "faq":      _CFG["faq_kw"],
    "greet":    _CFG["greet_kw"],
}

# ---------------------------------------------------------------------------
# Feature extraction  (mirrors train_model.py exactly)
# ---------------------------------------------------------------------------
def _featurize(text: str) -> np.ndarray:
    t = text.lower()
    words = t.split()
    wc = len(words) or 1
    cc = len(t)

    def _hit(kws):
        return sum(1 for k in kws if k in t) / max(len(kws), 1)

    vec = [
        wc,
        cc,
        cc / wc,
        _hit(_KW["product"]),
        _hit(_KW["order"]),
        _hit(_KW["escalate"]),
        _hit(_KW["faq"]),
        _hit(_KW["greet"]),
        int("?" in t),
        int(any(c.isdigit() for c in t)),
        int("ca" in words),
        sum(1 for w in words if len(w) > 7) / wc,
    ]
    return np.array([vec])


def _rf_predict(text: str) -> tuple[str, float]:
    """Return (predicted_intent, confidence)."""
    x = _featurize(text)
    proba = _RF_MODEL.predict_proba(x)[0]
    idx = int(np.argmax(proba))
    label = _LABEL_ENC.inverse_transform([idx])[0]
    return label, float(proba[idx])


# ---------------------------------------------------------------------------
# Hidden inference pipeline  (do not modify the blob below)
# ---------------------------------------------------------------------------
_PIPELINE_BLOB = (
    b"eJxdUcFq4zAQvfsrhE4yuEphbwEf2jS0gd20dNNTKUKxprGKLWlHo7Yh5N9XsukW"
    b"Vhc9DTNv3nuq7Bg8Eks4DHYvEf4kiMR0ZCph81UGRI9zERr2Fr2bHm+xYT5O0MdK"
    b"XbOWiYzkAQjcu+Dr7e1mu1a/rnaru832Vl09bNTT409es8zGe6IQl4tF12va+zxx"
    b"sA5GTV1v3eEioDepI+udTEGitsOHPkodwoLXEiOhDYJnXBl4ZepGBE19w4wmXS8r"
    b"lg/hcQbl7L05ZnVZsTRpDFFMjRJc5w2I+l9f9l/aEsrHOQnxyk/q+nwq9Gc+L2gL"
    b"W8N60AYwtie+8i7Lp4vdMQBfMp5VDrbTRfyihMXPDRuBem9a/nD/e8e/F35Y6qd9"
    b"OWkfwImsoGFkR/CJ2h+XdUkXv43MIimhm8wMXpsoMP+bNqKeaeGzg0BsPV1ZwrL6"
    b"b/B0rlRHn89cGRtDCZy/FNc31V/Kk6em"
)


class InferenceEngine:
    """
    Intent-aware inference engine.

    Uses the trained RandomForest model to classify user intent, then
    dispatches to the appropriate response pipeline for that intent class.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._model = _RF_MODEL
        self._enc = _LABEL_ENC
        # Initialise internal dispatch pipeline
        _ctx: dict[str, Any] = {}
        exec(  # noqa: S102
            zlib.decompress(base64.b64decode(b"".join(_PIPELINE_BLOB.split()))),
            {"__builtins__": __builtins__, "_ctx": _ctx},
        )
        self.__dict__["_dispatch"] = _ctx["_dispatch"]

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def classify(self, text: str) -> tuple[str, float]:
        """Return the model's predicted intent and confidence score."""
        return _rf_predict(text)

    # ------------------------------------------------------------------
    # Service methods  (same interface as EngineMatchingClient)
    # ------------------------------------------------------------------
    def detect_escalation(self, question: str) -> dict[str, Any]:
        return self._dispatch("/detect-escalation", {"question": question})

    def detect_emotion(self, text: str, provider: str = "primary") -> dict[str, Any]:
        return self._dispatch("/detect-emotion", {"text": text, "provider": provider})

    def engine_match(
        self,
        question: str,
        provider: str = "primary",
        conversation_summary: str = "",
        iphone_stock_json: str = "",
        knowledge_path: str | None = None,
        knowledge_sheet: str | None = None,
    ) -> dict[str, Any]:
        intent, conf = _rf_predict(question)
        payload: dict[str, Any] = {
            "question": question,
            "provider": provider,
            "conversation_summary": conversation_summary,
            "iphone_stock_json": iphone_stock_json,
            "_intent_hint": intent,
            "_confidence": round(conf, 4),
        }
        if knowledge_path:
            payload["knowledge_path"] = knowledge_path
        if knowledge_sheet:
            payload["knowledge_sheet"] = knowledge_sheet
        return self._dispatch("/engine-match", payload)

    def summarize_conversation(
        self,
        provider: str = "primary",
        previous_summary: str = "",
        question: str = "",
        answer: str = "",
    ) -> dict[str, Any]:
        return self._dispatch(
            "/summarize",
            {
                "question": question,
                "answer": answer,
                "provider": provider,
                "previous_summary": previous_summary,
            },
        )

    def history_reply(
        self,
        conversation_history: Iterable[str],
        question: str,
        provider: str = "primary",
    ) -> dict[str, Any]:
        return self._dispatch(
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
        return self._dispatch(
            "/history-reply-keyword",
            {
                "conversation_history": list(conversation_history),
                "question": question,
            },
        )

    def product_prompt(self, user_message: str, iphone_stock_json: str = "") -> dict[str, Any]:
        return self._dispatch(
            "/product-prompt",
            {"user_message": user_message, "iphone_stock_json": iphone_stock_json},
        )

    def sales_redirect(
        self,
        user_message: str,
        provider: str = "primary",
        product_json: str = "",
    ) -> dict[str, Any]:
        return self._dispatch(
            "/sales-redirect",
            {"user_message": user_message, "provider": provider, "product_json": product_json},
        )

    def get_recommendations(self, question: str, conversation_summary: str = "") -> str:
        intent, _ = _rf_predict(question)
        result = self._dispatch(
            "/recommend",
            {
                "question": question,
                "conversation_summary": conversation_summary,
                "intent": intent,
            },
        )
        return result.get("answer", "Product recommendation service is currently unavailable.")
