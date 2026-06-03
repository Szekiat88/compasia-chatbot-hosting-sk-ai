"""WhatsApp Cloud API webhook."""
from __future__ import annotations

from _ai_config import PROVIDER_PRIMARY

from dataclasses import dataclass
import logging
import os
import random
import sys
import threading
import traceback
from threading import Lock
from collections import deque
import sqlite3

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from db_log_handler import DBLogHandler, setup_db_logging
from chat_api import run_search
from chat_services import create_conversation_record, ensure_customer_record, store_error_record
from product_webhook import marketplace_bp

load_dotenv()

app = Flask(__name__)
app.register_blueprint(marketplace_bp)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
setup_db_logging()
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("whatsapp_webhook")


def set_log_sender(phone_number: str | None) -> None:
    DBLogHandler.set_sender(phone_number)


def clear_log_sender() -> None:
    DBLogHandler.clear_sender()


@app.errorhandler(Exception)
def handle_unhandled_exception(exc: Exception):
    logger.exception("unhandled_exception path=%s", request.path)
    return jsonify({"error": "Internal server error"}), 500


logger.info("startup python_executable=%s", sys.executable)


@app.before_request
def trace_incoming_request() -> None:
    logger.info(
        "http_inbound method=%s path=%s remote=%s",
        request.method,
        request.path,
        request.remote_addr,
    )
    if request.path == "/webhook" and request.method == "GET":
        logger.info(
            "webhook_verify_probe args=%s",
            sorted(request.args.keys()),
        )
    if request.path == "/webhook" and request.method == "POST":
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            logger.info("webhook_post_payload_keys=%s", sorted(payload.keys()))
        else:
            logger.info("webhook_post_payload_keys=non_json_or_empty")


@dataclass
class ConversationState:
    conversation_id: str | None = None
    summary: str = ""
    customer_id: str | None = None
    customer_no: str | None = None


conversation_state_by_sender: dict[str, ConversationState] = {}
state_lock = Lock()
processed_message_ids: set[str] = set()
processed_message_order: deque[str] = deque()
processed_lock = Lock()
MAX_PROCESSED_MESSAGE_IDS = 5000
SEEN_DB_PATH = os.getenv("WHATSAPP_SEEN_DB_PATH", "wa_webhook_seen.db")
seen_db_init_lock = Lock()
seen_db_ready = False


WAIT_MESSAGES = [
    "Thank you for your patience! We're looking into your query right now. 🙏",
    "Still on it! Please bear with us for a moment. ⏳",
    "We're working on your request — won't be long! 😊",
    "Almost there! Just give us a little more time. 🔍",
    "Our team is on it! Please wait while we find the best answer for you. 💪",
    "Thanks for waiting! We're processing your query now. 🚀",
    "Hang tight! We'll have an answer for you very shortly. ✨",
    "We appreciate your patience! We're checking the details for you. 🛠️",
]


def _send_wait_messages_loop(to_number: str, stop_event: threading.Event, initial_delay: int = 5, interval: int = 20) -> None:
    """Send a wait message after initial_delay seconds, then every interval seconds."""
    if stop_event.wait(timeout=initial_delay):
        return
    try:
        send_whatsapp_text(to_number, random.choice(WAIT_MESSAGES))
    except Exception:
        logger.exception("wa_wait_message_failed to=%s", to_number)
    while not stop_event.wait(timeout=interval):
        try:
            send_whatsapp_text(to_number, random.choice(WAIT_MESSAGES))
        except Exception:
            logger.exception("wa_wait_message_failed to=%s", to_number)


def get_whatsapp_api_base() -> str:
    version = os.getenv("WHATSAPP_API_VERSION", "v19.0")
    base = os.getenv("WHATSAPP_API_BASE", "https://graph.facebook.com")
    return f"{base}/{version}".rstrip("/")


def split_on_empty_rows(text: str) -> list[str]:
    """Split an answer at blank lines so each paragraph becomes its own message."""
    import re
    parts = re.split(r'\n[ \t]*\n', text.strip())
    return [p.strip() for p in parts if p.strip()]


def split_for_whatsapp(body: str, max_chars: int = 3900) -> list[str]:
    text = (body or "").strip()
    if not text:
        return ["Sorry, I could not generate a reply."]
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_chars)
        if cut < 1:
            cut = remaining.rfind(" ", 0, max_chars)
        if cut < 1:
            cut = max_chars
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return chunks


def send_whatsapp_text(to_number: str, body: str) -> None:
    token = os.getenv("WHATSAPP_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_number_id:
        raise RuntimeError("Missing WHATSAPP_TOKEN or WHATSAPP_PHONE_NUMBER_ID.")

    url = f"{get_whatsapp_api_base()}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    for index, part in enumerate(split_for_whatsapp(body), start=1):
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": part},
        }
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        if response.status_code >= 300:
            logger.error(
                "wa_outbound_failed to=%s part=%s code=%s body=%s",
                to_number,
                index,
                response.status_code,
                response.text,
            )
        else:
            logger.info("wa_outbound_ok to=%s part=%s code=%s", to_number, index, response.status_code)


def extract_messages(payload: dict) -> list[dict]:
    messages: list[dict] = []
    
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata", {})
            for message in value.get("messages", []) or []:
                text = message.get("text", {}).get("body")
                sender = message.get("from")
                msg_id = message.get("id")
                contacts = value.get("contacts") or []
                profile_name = None
                if contacts:
                    profile_name = contacts[0].get("profile", {}).get("name")
                if sender and text:
                    messages.append(
                        {
                            "from": sender,
                            "text": text,
                            "id": msg_id,
                            "name": profile_name,
                            "display_phone_number": metadata.get("display_phone_number"),
                            "phone_number_id": metadata.get("phone_number_id"),
                        }
                    )
    return messages


def ensure_seen_db() -> None:
    global seen_db_ready
    if seen_db_ready:
        return
    with seen_db_init_lock:
        if seen_db_ready:
            return
        with sqlite3.connect(SEEN_DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_whatsapp_messages (
                    message_id TEXT PRIMARY KEY,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        seen_db_ready = True


def persist_message_id_once(message_id: str) -> bool:
    """Return True only on first insert of message_id in persistent store."""

    ensure_seen_db()
    try:
        with sqlite3.connect(SEEN_DB_PATH) as conn:
            conn.execute(
                "INSERT INTO processed_whatsapp_messages (message_id) VALUES (?)",
                (message_id,),
            )
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception:
        logger.exception("wa_seen_db_error message_id=%s", message_id)
        # Fallback to in-memory behavior when DB is unavailable.
        return True


def mark_message_processed_once(message_id: str | None) -> bool:
    """Return True only for the first time we see a given WhatsApp message id."""

    if not message_id:
        return True

    with processed_lock:
        if message_id in processed_message_ids:
            return False

    if not persist_message_id_once(message_id):
        with processed_lock:
            processed_message_ids.add(message_id)
        return False

    with processed_lock:
        processed_message_ids.add(message_id)
        processed_message_order.append(message_id)
        if len(processed_message_order) > MAX_PROCESSED_MESSAGE_IDS:
            old = processed_message_order.popleft()
            processed_message_ids.discard(old)
    return True


def get_or_create_conversation(sender: str, profile_name: str | None) -> ConversationState:
    with state_lock:
        existing = conversation_state_by_sender.get(sender)
        if existing:
            return existing

    customer = ensure_customer_record(name=profile_name, phone=sender)
    conversation_id = None
    customer_id = None
    customer_no = None
    if customer:
        customer_id, customer_no = customer
        conversation_id = create_conversation_record(customer_id, customer_no)

    state = ConversationState(
        conversation_id=conversation_id,
        summary="",
        customer_id=customer_id,
        customer_no=customer_no,
    )
    with state_lock:
        conversation_state_by_sender[sender] = state
    return state


def build_brain_reply(sender: str, profile_name: str | None, text: str) -> tuple[list[str], ConversationState]:
    state = get_or_create_conversation(sender, profile_name)

    logger.info(
        "brain_request sender=%s conversation_id=%s summary_len=%s",
        sender,
        state.conversation_id,
        len(state.summary),
    )
    result = run_search(
        question=text,
        conversation_history=state.summary,
        provider=os.getenv("MODEL_PROVIDER", PROVIDER_PRIMARY),
        conversation_id=state.conversation_id,
    )
    if "marketplace_order_id" in result and not result.get("marketplace_found"):
        logger.warning(
            "marketplace_order_not_found sender=%s order_id=%s reason=%s",
            sender,
            result["marketplace_order_id"],
            result.get("marketplace_failure_reason", "unknown"),
        )
    answer = str(result.get("answer") or "Sorry, I could not find an answer right now.")
    new_summary = result.get("conversation_summary")
    if isinstance(new_summary, str):
        state.summary = new_summary

    parts = split_on_empty_rows(answer)
    if not parts:
        parts = [answer]

    logger.info(
        "brain_response sender=%s conversation_id=%s ticket_logged=%s answer_len=%s parts=%s",
        sender,
        state.conversation_id,
        bool(result.get("ticket_logged")),
        len(answer),
        len(parts),
    )
    return parts, state


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok"}, 200


@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and verify_token == expected_token:
        return challenge or "", 200
    return "Verification failed", 403


@app.post("/webhook")
def receive_webhook():
    payload = request.get_json(silent=True) or {}
    messages = extract_messages(payload)

    if not messages:
        logger.info("webhook_non_message_event payload_keys=%s", list(payload.keys()))
        return jsonify({"status": "ok"}), 200

    logger.info("webhook_messages_received count=%s", len(messages))
    for msg in messages:
        sender = msg["from"]
        text = msg["text"]
        profile_name = msg.get("name")
        message_id = msg.get("id")
        phone_number_id = msg.get("phone_number_id")
        display_phone_number = msg.get("display_phone_number")

        logger.info(
            "wa_inbound_meta display_number=%s phone_number_id=%s",
            display_phone_number,
            phone_number_id,
        )

        allowed_phone_number_id = os.getenv("PRIMARY_PHONE_NUMBER_ID")
        if allowed_phone_number_id and phone_number_id and phone_number_id != allowed_phone_number_id:
            logger.info(
                "wa_skipped_other_number sender=%s phone_number_id=%s allowed=%s",
                sender,
                phone_number_id,
                allowed_phone_number_id,
            )
            continue

        logger.info(
            "wa_inbound sender=%s name=%s message_id=%s text=%s",
            sender,
            profile_name or "",
            message_id or "",
            text,
        )
        if not mark_message_processed_once(message_id):

            logger.info("wa_duplicate_ignored sender=%s message_id=%s", sender, message_id)
            continue
        set_log_sender(sender)
        stop_event = threading.Event()
        wait_thread = threading.Thread(
            target=_send_wait_messages_loop,
            args=(sender, stop_event),
            daemon=True,
        )
        wait_thread.start()
        try:
            reply_parts, state = build_brain_reply(sender, profile_name, text)
            logger.info(
                "wa_reply_ready sender=%s conversation_id=%s parts=%s total_len=%s",
                sender,
                state.conversation_id,
                len(reply_parts),
                sum(len(p) for p in reply_parts),
            )
            for part in reply_parts:
                send_whatsapp_text(sender, part)
        except Exception:
            err = traceback.format_exc()
            logger.exception("wa_brain_failed sender=%s user_message=%r", sender, text)
            existing_state = conversation_state_by_sender.get(sender)
            conv_id = existing_state.conversation_id if existing_state else None
            store_error_record(conv_id, text, err)
            try:
                send_whatsapp_text(
                    sender,
                    "Sorry, I hit an internal error while processing your message. Please try again.",
                )
            except Exception:
                logger.exception("wa_fallback_send_failed sender=%s user_message=%r", sender, text)
        finally:
            stop_event.set()
            wait_thread.join(timeout=5)
            clear_log_sender()

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.getenv("WHATSAPP_PORT", "5052"))
    debug = os.getenv("WHATSAPP_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
