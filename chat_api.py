"""Flask API for the CompAsia chat search service."""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, render_template, request

from db_log_handler import setup_db_logging
from _ai_config import PROVIDER_PRIMARY
from chat_services import search as _search

app = Flask(__name__)
setup_db_logging()
logger = logging.getLogger("flask_api")


def run_search(
    question: str,
    conversation_history: str,
    provider: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, str | None]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question cannot be empty.")

    resolved_provider = provider or os.getenv("MODEL_PROVIDER", PROVIDER_PRIMARY)
    result = _search(
        question,
        resolved_provider,
        conversation_summary=conversation_history,
        conversation_id=conversation_id,
    )
    if not result:
        raise RuntimeError("Search returned no data.")

    return result


@app.get("/")
def health() -> tuple[dict[str, str], int]:
    """Basic health check endpoint."""
    return {"status": "ok"}, 200


@app.get("/chat")
def chat_page() -> str:
    """Serve a simple web UI for asking chatbot questions."""
    return render_template("chat.html")


@app.post("/search")
def search_keyword() -> tuple[dict[str, str | None], int]:

    payload = request.get_json(silent=True) or {}
    question = payload.get("question", "")
    conversation_summary = payload.get("conversation_history", "")
    conversation_id = payload.get("conversation_id")

    try:
        result = run_search(
            question=question,
            conversation_history=conversation_summary,
            provider=payload.get("provider"),
            conversation_id=conversation_id,
        )
    except ValueError as exc:
        logger.exception("search_bad_request question=%r", question)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("search_failed question=%r", question)
        return jsonify({"error": str(exc)}), 500

    return jsonify(result), 200


@app.errorhandler(Exception)
def handle_unhandled_exception(exc: Exception):
    logger.exception("unhandled_exception path=%s", request.path)
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051)
