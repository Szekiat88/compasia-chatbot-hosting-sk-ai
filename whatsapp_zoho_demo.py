"""Demo webhook: create Zoho Desk ticket when WhatsApp message says "create ticket"."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from zoho_ticket_creation import (
    DEPT_GENERAL,
    PRIORITY_MEDIUM,
    create_zoho_ticket,
)

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("whatsapp_zoho_demo")


def get_whatsapp_api_base() -> str:
    version = os.getenv("WHATSAPP_API_VERSION", "v19.0")
    base = os.getenv("WHATSAPP_API_BASE", "https://graph.facebook.com")
    return f"{base}/{version}".rstrip("/")


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
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": body},
    }
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    if response.status_code >= 300:
        logger.error("WhatsApp send failed: %s %s", response.status_code, response.text)
    else:
        logger.info("WhatsApp send ok: %s", response.status_code)


def extract_messages(payload: dict) -> list[dict]:
    messages: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
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
                        }
                    )
    return messages


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


def should_create_ticket(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return normalized == "create ticket"


@app.post("/webhook")
def receive_webhook():
    payload = request.get_json(silent=True) or {}
    logger.info("Webhook hit. Raw payload: %s", payload)
    for msg in extract_messages(payload):
        sender = msg["from"]
        text = msg["text"]
        name = msg.get("name") or "WhatsApp User"

        logger.info("Incoming message from %s: %s", sender, text)

        if should_create_ticket(text):
            try:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                subject = f"WhatsApp ticket request - {name}"
                description = (
                    f"Customer requested ticket creation via WhatsApp.\n"
                    f"Name: {name}\n"
                    f"Phone: {sender}\n"
                    f"Message: {text}\n"
                    f"Received: {now}"
                )
                ticket = create_zoho_ticket(
                    subject=subject,
                    description=description,
                    department_id=DEPT_GENERAL,
                    priority=PRIORITY_MEDIUM,
                )
                ticket_id = ticket.get("id", "unknown")
                send_whatsapp_text(
                    sender,
                    f"Your ticket has been created. Ticket ID: {ticket_id}",
                )
            except Exception as exc:
                logger.exception("Ticket creation failed.")
                send_whatsapp_text(
                    sender,
                    "Sorry, we could not create your ticket. Please try again later.",
                )
        else:
            send_whatsapp_text(
                sender,
                'Send "create ticket" to open a Zoho Desk ticket.',
            )

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.getenv("WHATSAPP_PORT", "5052"))
    app.run(host="0.0.0.0", port=port)
