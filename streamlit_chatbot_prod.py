"""Simple Streamlit UI that escalates to Zoho Desk when the user asks for an agent."""

from __future__ import annotations

import re
import time
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path

import streamlit as st
import psycopg2

from chatbot_prod_testing import (
    create_zoho_ticket,
    fetch_public_comments,
    format_transcript,
    needs_escalation,
)
from message_store import append_agent_message, fetch_agent_messages
from shopify_stock_service import ShopifyStockClient, fetch_stock_listing
from shopify_trackker import ACCESS_TOKEN, SHOP_DOMAIN, get_order_detail

YES_ANSWERS = {"yes", "y", "sure", "ok", "okay", "please", "send", "yah", "ya"}
NO_ANSWERS = {"no", "n", "nope", "nah", "not now"}
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
ORDER_RE = re.compile(r"#\w+", re.IGNORECASE)


def load_gmail_sender() -> callable:
    module_path = Path(__file__).with_name("smtp-gmail.py")
    spec = spec_from_file_location("smtp_gmail", module_path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load smtp-gmail.py.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "send_email"):
        raise RuntimeError("smtp-gmail.py is missing send_email.")
    return module.send_email


def ensure_state() -> None:
    if "conversation" not in st.session_state:
        st.session_state.conversation = []
    if "customer_id" not in st.session_state:
        st.session_state.customer_id = None
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = None
    if "customer_name" not in st.session_state:
        st.session_state.customer_name = ""
    if "customer_phone" not in st.session_state:
        st.session_state.customer_phone = ""
    if "last_ticket_id" not in st.session_state:
        st.session_state.last_ticket_id = None
    if "last_ticket_display" not in st.session_state:
        st.session_state.last_ticket_display = None
    if "agent_last_seen_id" not in st.session_state:
        st.session_state.agent_last_seen_id = {}
    if "agent_last_seen_comment_id" not in st.session_state:
        st.session_state.agent_last_seen_comment_id = {}
    if "pending_email_offer" not in st.session_state:
        st.session_state.pending_email_offer = False
    if "pending_email_request" not in st.session_state:
        st.session_state.pending_email_request = False
    if "last_order_details" not in st.session_state:
        st.session_state.last_order_details = ""
    if "last_order_name" not in st.session_state:
        st.session_state.last_order_name = ""


def render_messages() -> None:
    for author, text in st.session_state.conversation:
        role = "user" if author == "user" else "assistant"
        with st.chat_message(role):
            if isinstance(text, dict) and text.get("type") == "stock_tables":
                st.write(f"In-stock options for: {text.get('query', 'your request')}")
                for table in text.get("tables") or []:
                    st.write(table.get("title") or "Listings")
                    st.dataframe(table.get("rows") or [], use_container_width=True)
                if text.get("collection_link"):
                    st.write(f"Collection: {text['collection_link']}")
            else:
                st.write(text)


def load_db_env() -> dict[str, str]:
    env_path = Path(__file__).with_name("db.env")
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
        st.error(f"Missing DB settings: {', '.join(missing)}")
        return None
    try:
        return psycopg2.connect(**config)
    except Exception as exc:
        st.error(f"Database connection failed: {exc}")
        return None


def ensure_customer_record(name: str, phone: str) -> str | None:
    conn = with_db_connection()
    if not conn:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                if phone:
                    cur.execute(
                        "SELECT customer_id FROM customer WHERE phone = %s ORDER BY created_at DESC LIMIT 1",
                        (phone,),
                    )
                    row = cur.fetchone()
                    if row:
                        return str(row[0])
                cur.execute(
                    "INSERT INTO customer (name, phone) VALUES (%s, %s) RETURNING customer_id",
                    (name, phone),
                )
                return str(cur.fetchone()[0])
    finally:
        conn.close()


def create_conversation_record(customer_id: str) -> str | None:
    conn = with_db_connection()
    if not conn:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_conversation (customer_id) VALUES (%s) RETURNING conversation_id",
                    (customer_id,),
                )
                return str(cur.fetchone()[0])
    finally:
        conn.close()


def store_message_record(conversation_id: str, question: str, answer: str) -> None:
    conn = with_db_connection()
    if not conn:
        return
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_message (conversation_id, question, answer) VALUES (%s, %s, %s)",
                    (conversation_id, question, answer),
                )
    finally:
        conn.close()


def format_bot_reply(text: object) -> str:
    if isinstance(text, dict) and text.get("type") == "stock_tables":
        query = text.get("query", "request")
        return f"In-stock options for: {query}"
    return str(text)


def extract_email(text: str) -> str | None:
    match = EMAIL_RE.search(text)
    return match.group(0) if match else None


def extract_order_token(text: str) -> str | None:
    match = ORDER_RE.search(text)
    return match.group(0).upper() if match else None


def should_check_stock(text: str) -> bool:
    lowered = text.lower()
    return "iphone" in lowered


def handle_message(user_text: str) -> None:
    st.session_state.conversation.append(("user", user_text))
    bot_replies: list[str] = []

    def add_bot_reply(reply: object) -> None:
        st.session_state.conversation.append(("bot", reply))
        bot_replies.append(format_bot_reply(reply))

    def finalize() -> None:
        if st.session_state.conversation_id and bot_replies:
            store_message_record(
                st.session_state.conversation_id,
                user_text,
                "\n".join(bot_replies),
            )

    if st.session_state.pending_email_request:
        email = extract_email(user_text)
        if not email:
            add_bot_reply("Please share a valid email address.")
            finalize()
            return
        try:
            send_email = load_gmail_sender()
            subject = f"Order details {st.session_state.last_order_name}".strip()
            send_email(email, subject, st.session_state.last_order_details)
            add_bot_reply(f"Sent! I emailed the order details to {email}.")
        except Exception as exc:
            add_bot_reply(f"Email failed: {exc}")
        finally:
            st.session_state.pending_email_request = False
            st.session_state.pending_email_offer = False
        finalize()
        return

    if st.session_state.pending_email_offer:
        normalized = user_text.lower().strip()
        if normalized in YES_ANSWERS:
            st.session_state.pending_email_request = True
            st.session_state.pending_email_offer = False
            add_bot_reply("Sure - what email should I send it to?")
            finalize()
            return
        if normalized in NO_ANSWERS:
            st.session_state.pending_email_offer = False
            add_bot_reply("Okay, I won't send an email.")
            finalize()
            return
        add_bot_reply("Please reply with yes or no.")
        finalize()
        return

    if needs_escalation(user_text):
        transcript = format_transcript(st.session_state.conversation)
        description = f"The customer asked for a human agent.\n\nTranscript:\n{transcript}"
        try:
            ticket = create_zoho_ticket("Chatbot escalation request", description)
            ticket_id = str(ticket.get("id") or ticket.get("ticketNumber"))
            ticket_number = ticket.get("ticketNumber") or ticket_id
            st.session_state.last_ticket_id = ticket_id
            st.session_state.last_ticket_display = ticket_number
            add_bot_reply(f"Escalated to Zoho Desk. Ticket: {ticket_number}")
        except Exception as exc:  # surface failure to the user
            add_bot_reply(f"Failed to create ticket: {exc}")
        finalize()
        return

    order_token = extract_order_token(user_text)
    if order_token:
        try:
            details = get_order_detail(order_token)
            st.session_state.last_order_details = details
            st.session_state.last_order_name = order_token
            add_bot_reply(f"Order details:\n{details}")
            st.session_state.pending_email_offer = True
            add_bot_reply("Do you want me to send to your email? (yes/no)")
        except Exception as exc:
            add_bot_reply(f"Order lookup failed: {exc}")
        finalize()
        return

    if should_check_stock(user_text):
        try:
            stock_client = ShopifyStockClient(shop_domain=SHOP_DOMAIN, access_token=ACCESS_TOKEN)
            stock = fetch_stock_listing(user_text, bot=stock_client)
            tables = stock.get("tables") or []
            if not tables:
                add_bot_reply("I couldn't find in-stock listings for that request.")
                finalize()
                return
            add_bot_reply(
                {
                    "type": "stock_tables",
                    "query": user_text,
                    "tables": tables,
                    "collection_link": stock.get("collection_link"),
                }
            )
        except Exception as exc:
            add_bot_reply(f"Stock lookup failed: {exc}")
        finalize()
        return

    bot_reply = "Noted. Say 'agent' or 'human' anytime to reach our team."
    add_bot_reply(bot_reply)
    finalize()


def poll_zoho_comments(ticket_id: str) -> None:
    after_comment_id = st.session_state.agent_last_seen_comment_id.get(ticket_id)
    try:
        comments = fetch_public_comments(str(ticket_id), after_comment_id=after_comment_id)
    except Exception as exc:
        st.sidebar.warning(f"Polling failed: {exc}")
        return

    for comment in comments:
        append_agent_message(str(ticket_id), comment["content"])
        st.session_state.agent_last_seen_comment_id[ticket_id] = comment["id"]


def load_new_agent_replies(polling_enabled: bool) -> None:
    ticket_id = st.session_state.last_ticket_id
    if not ticket_id:
        return

    if polling_enabled:
        poll_zoho_comments(ticket_id)

    after_id = st.session_state.agent_last_seen_id.get(ticket_id)
    new_messages = fetch_agent_messages(str(ticket_id), after_id)
    if not new_messages:
        return

    for msg in new_messages:
        st.session_state.conversation.append(("bot", f"Agent: {msg['message']}"))
        st.session_state.agent_last_seen_id[ticket_id] = msg["id"]


def main() -> None:
    st.set_page_config(page_title="Support Chat", page_icon="💬", layout="centered")
    st.markdown(
        """
        <style>
        body { background: #e5ddd5; }
        .stApp { background: #e5ddd5; }
        .stChatMessage { border-radius: 16px; }
        .stChatMessage[data-testid="stChatMessage-user"] {
            background: #dcf8c6;
            border: 1px solid #c0e7ad;
        }
        .stChatMessage[data-testid="stChatMessage-assistant"] {
            background: #ffffff;
            border: 1px solid #e5e7eb;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("Integration Testing")
    st.caption("Testing of Shopify Order ID, Stock Availibility, SMTP Gmail, Zoho Desk")

    ensure_state()
    polling_enabled = True
    poll_interval = 6

    if not st.session_state.conversation_id:
        st.subheader("Start the bot")
        st.write("Please fill in your name and number, for history purpose.")
        with st.form("customer_form"):
            name = st.text_input("Name", value=st.session_state.customer_name)
            phone = st.text_input("Phone number", value=st.session_state.customer_phone)
            submitted = st.form_submit_button("Start chat")
        if submitted:
            if not name.strip() or not phone.strip():
                st.error("Please enter both name and phone number.")
                return
            customer_id = ensure_customer_record(name.strip(), phone.strip())
            if not customer_id:
                return
            conversation_id = create_conversation_record(customer_id)
            if not conversation_id:
                return
            st.session_state.customer_id = customer_id
            st.session_state.conversation_id = conversation_id
            st.session_state.customer_name = name.strip()
            st.session_state.customer_phone = phone.strip()
            st.session_state.conversation.append(("bot", "Hi there, how can I help you?"))
            st.rerun()
        return

    load_new_agent_replies(polling_enabled)
    render_messages()

    if st.session_state.last_ticket_id:
        display = st.session_state.last_ticket_display or st.session_state.last_ticket_id
        st.caption(f"Ticket: {display}")

    if prompt := st.chat_input("Type a message"):
        handle_message(prompt.strip())
        st.rerun()

    if st.session_state.last_ticket_id:
        time.sleep(poll_interval)
        st.rerun()


if __name__ == "__main__":
    main()
