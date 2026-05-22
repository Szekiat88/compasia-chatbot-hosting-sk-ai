"""Streamlit chatbot with AI-powered search and conversation memory."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from typing import Iterable

import psycopg2
import streamlit as st

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("streamlit_chat")

from _ai_config import PROVIDER_PRIMARY
from chat_services import search as _search
from conversation_ids import conversation_no, customer_no


def has_streamlit_runtime() -> bool:
    """Return True when executed via `streamlit run`.

    Running this script directly with `python streamlit_chat.py` produces repeated
    warnings and broken session state. Early-exit when the Streamlit runtime is
    unavailable to keep the CLI experience clean and point users to the intended
    invocation method.
    """

    runtime = getattr(st, "runtime", None)
    exists = getattr(runtime, "exists", None)
    if callable(exists):
        try:
            return bool(exists())
        except Exception:  # pragma: no cover - defensive guard
            return False
    return False


def ensure_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "summary" not in st.session_state:
        st.session_state.summary = ""
    if "customer_id" not in st.session_state:
        st.session_state.customer_id = None
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = None
    if "customer_name" not in st.session_state:
        st.session_state.customer_name = ""
    if "customer_phone" not in st.session_state:
        st.session_state.customer_phone = ""


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
        log.error("Missing DB settings: %s", missing)
        st.error(f"Missing DB settings: {', '.join(missing)}")
        return None
    log.info("Connecting to DB at %s:%s/%s...", config["host"], config["port"], config["dbname"])
    try:
        conn = psycopg2.connect(**config)
        log.info("DB connected OK")
        return conn
    except Exception as exc:
        log.error("DB connection failed: %s", exc)
        st.error(f"Database connection failed: {exc}")
        return None


def ensure_customer_record(
    name: str | None = None,
    phone: str | None = None,
) -> tuple[str, str] | None:
    log.info("Looking up customer: name=%s phone=%s", name, phone)
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
                        log.info("Existing customer found: cust_id=%s", row[0])
                        if clean_name:
                            cur.execute(
                                "UPDATE customer SET cust_name = %s WHERE cust_id = %s",
                                (clean_name, row[0]),
                            )
                        return str(row[0]), str(row[1])
                log.info("New customer — inserting record...")
                new_customer_no = customer_no()
                cur.execute(
                    "INSERT INTO customer (cust_name, cust_phone, cust_no) VALUES (%s, %s, %s) RETURNING cust_id",
                    (clean_name, clean_phone, new_customer_no),
                )
                new_id = str(cur.fetchone()[0])
                log.info("New customer created: cust_id=%s cust_no=%s", new_id, new_customer_no)
                return new_id, new_customer_no
    finally:
        conn.close()


def create_conversation_record(customer_id: str | None, customer_no_value: str | None) -> str | None:
    log.info("Creating conversation record for cust_id=%s", customer_id)
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
                conv_id = str(cur.fetchone()[0])
                log.info("Conversation created: conv_id=%s", conv_id)
                return conv_id
    finally:
        conn.close()


def ensure_conversation_session(name: str, phone: str) -> bool:
    if st.session_state.conversation_id:
        return True
    customer_result = ensure_customer_record(name, phone)
    if not customer_result:
        return False
    customer_id, customer_no_value = customer_result
    conversation_id = create_conversation_record(customer_id, customer_no_value)
    if not conversation_id:
        return False
    st.session_state.customer_id = customer_id
    st.session_state.conversation_id = conversation_id
    return True


def render_customer_gate() -> bool:
    if st.session_state.conversation_id:
        return True

    st.subheader("Start chat")
    st.write("Please enter your name and phone number before chatting.")
    with st.form("customer_details_form"):
        name = st.text_input("Name", value=st.session_state.customer_name)
        phone = st.text_input("Phone number", value=st.session_state.customer_phone)
        submitted = st.form_submit_button("Start chatting")

    if not submitted:
        return False

    clean_name = name.strip()
    clean_phone = phone.strip()

    if not clean_name or not clean_phone:
        st.error("Name and phone number are required.")
        return False

    st.session_state.customer_name = clean_name
    st.session_state.customer_phone = clean_phone

    if not ensure_conversation_session(clean_name, clean_phone):
        return False

    st.rerun()
    return False


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


def append_message(role: str, content: str) -> None:
    """Store a chat message while avoiding consecutive duplicates.

    Streamlit reruns the script on each interaction, and occasionally a prompt
    can be picked up twice (for example, when a user submits while a rerun is
    already queued). Guarding against consecutive duplicates keeps the chat
    history tidy and prevents repeated backend calls for the same input.
    """

    content = content.strip()
    if not content:
        return

    messages: list[dict[str, str]] = st.session_state.messages
    if messages and messages[-1]["role"] == role and messages[-1]["content"] == content:
        return

    messages.append({"role": role, "content": content})


def render_history() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            render_bubble(message["content"], message["role"])


def format_history_for_api(messages: Iterable[dict[str, str]]) -> list[str]:
    """Convert stored messages into the backend-friendly format."""

    return [item.get("content", "") for item in messages if item.get("role") != "system"]


def ask_backend(question: str) -> tuple[str, bool]:
    log.info("── ask_backend() ────────────────────────────")
    log.info("Question      : %s", question)
    log.info("Conv ID       : %s", st.session_state.conversation_id)
    log.info("Summary so far: %s", st.session_state.summary[:120] if st.session_state.summary else "(none)")

    data = _search(
        user_question=question,
        engine_mode=PROVIDER_PRIMARY,
        conversation_summary=st.session_state.summary,
        conversation_id=st.session_state.conversation_id,
    )

    answer = data.get("answer")
    summary = data.get("conversation_summary")
    ticket_logged = bool(data.get("ticket_logged"))
    processing_ms = data.get("processing_time_ms", "?")

    log.info("Response received in %s ms | ticket_logged=%s", processing_ms, ticket_logged)

    if summary:
        st.session_state.summary = str(summary)
        log.info("Summary updated: %s", str(summary)[:120])

    if not answer:
        log.warning("Backend returned no answer")
        return "No answer provided by the backend.", ticket_logged

    return str(answer), ticket_logged


def apply_whatsapp_theme() -> None:
    st.markdown(
        """
        <style>
            body {}
            [data-testid="stAppViewContainer"] {
            }
            .main .block-container {
                padding-top: 1.5rem;
                padding-bottom: 2rem;
            }
            .chat-row {
                display: flex;
                margin: 0.35rem 0;
            }
            .chat-row.user {justify-content: flex-end;}
            .chat-row.assistant {justify-content: flex-start;}
            .chat-row .bubble {
                max-width: 80%;
                padding: 0.6rem 0.9rem;
                border-radius: 0.8rem;
                box-shadow: 0 1px 1px rgba(0, 0, 0, 0.05);
                font-size: 0.95rem;
                line-height: 1.4;
            }
            .chat-row.user .bubble {
                border: 1px solid;
                border-bottom-right-radius: 0.25rem;
            }
            .chat-row.assistant .bubble {
                border: 1px solid #d9d9d9;
                border-bottom-left-radius: 0.25rem;
            }
            .chat-row .bubble code {
                padding: 0.1rem 0.25rem;
                border-radius: 0.2rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_bubble(content: str, role: str) -> None:
    alignment = "user" if role == "user" else "assistant"
    if "<table" in content:
        safe_content = content
        st.markdown(
            f'<div class="chat-row {alignment}"><div class="bubble">{safe_content}</div></div>',
            unsafe_allow_html=True,
        )
        return
    st.markdown(content)


def main() -> None:
    if not has_streamlit_runtime():
        print("This app must be launched with 'streamlit run streamlit_chat.py'.")
        return

    st.set_page_config(page_title="Compasia Chatbot", page_icon="🤖")
    st.title("Compasia Chatbot")
    st.caption(
        "Ask questions, keep context, and let the backend engine handle the search logic."
    )

    ensure_state()
    if not render_customer_gate():
        return
    apply_whatsapp_theme()

    with st.sidebar:
        st.header("Conversation summary")
        st.write(
            st.session_state.summary or "Summary will appear after the first reply."
        )
        st.divider()
        if st.button("Clear conversation"):
            st.session_state.clear()
            ensure_state()
            st.toast("Conversation cleared", icon="🧹")
        if st.button("End conversation"):
            if st.session_state.conversation_id:
                update_conversation_summary(
                    st.session_state.conversation_id,
                    st.session_state.summary,
                )
            st.session_state.clear()
            ensure_state()
            st.toast("Conversation ended", icon="✅")

    chat_area = st.container()
    # with chat_area:
    #     render_history()

    if prompt := st.chat_input("Send a message"):
        append_message("user", prompt)
        chat_area.empty()
        with chat_area:
            render_history()
            with st.chat_message("assistant"):
                try:
                    with st.spinner("Thinking..."):
                        reply, ticket_logged = ask_backend(prompt)
                    render_bubble(reply, "assistant")
                    append_message("assistant", reply)
                    if ticket_logged and st.session_state.conversation_id:
                        update_conversation_summary(
                            st.session_state.conversation_id,
                            st.session_state.summary,
                        )
                except (ValueError, RuntimeError) as exc:  # pragma: no cover - runtime guard
                    st.error(f"Backend request failed: {exc}")


if __name__ == "__main__":
    main()
