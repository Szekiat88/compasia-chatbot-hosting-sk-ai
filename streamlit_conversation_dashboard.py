"""Internal dashboard to inspect chatbot conversation history by customer phone."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ConversationView:
    conv_id: str
    conv_no: str | None
    created_at: datetime | None
    summary: str | None
    messages: list[dict[str, str]]


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


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
        return cur.fetchone()[0] is not None


def get_existing_columns(conn, table_name: str, candidates: list[str]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        found = {row[0] for row in cur.fetchall()}
    return {name for name in candidates if name in found}


def resolve_message_table(conn) -> str | None:
    for name in ("chat_message_logs", "chat_message_log"):
        if table_exists(conn, name):
            return name
    return None


def fetch_customers_by_phone(conn, phone: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cust_id, cust_no, cust_name, cust_phone, created_at
            FROM customer
            WHERE cust_phone = %s
            ORDER BY created_at DESC NULLS LAST, cust_id DESC
            """,
            (phone,),
        )
        rows = cur.fetchall()

    customers = []
    for row in rows:
        customers.append(
            {
                "cust_id": str(row[0]),
                "cust_no": str(row[1]) if row[1] is not None else None,
                "cust_name": row[2] or "-",
                "cust_phone": row[3] or "-",
                "created_at": row[4],
            }
        )
    return customers


def fetch_conversations_for_customers(conn, customer_ids: list[str]) -> list[dict[str, Any]]:
    if not customer_ids:
        return []

    customer_ids_as_text = [str(item) for item in customer_ids]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT conv_id, conv_no, cust_id, created_at, conv_summary
            FROM chat_conversation
            WHERE cust_id::text = ANY(%s)
            ORDER BY created_at ASC NULLS LAST, conv_id ASC
            """,
            (customer_ids_as_text,),
        )
        rows = cur.fetchall()

    conversations = []
    for row in rows:
        conversations.append(
            {
                "conv_id": str(row[0]),
                "conv_no": str(row[1]) if row[1] is not None else None,
                "cust_id": str(row[2]) if row[2] is not None else None,
                "created_at": row[3],
                "summary": row[4],
            }
        )
    return conversations


def fetch_messages_for_conversation(conn, table_name: str, conv_id: str) -> list[dict[str, str]]:
    candidate_columns = [
        "message_question",
        "message_answer",
        "created_at",
        "updated_at",
        "message_no",
        "msg_id",
        "message_id",
        "id",
    ]
    available = get_existing_columns(conn, table_name, candidate_columns)
    select_cols = ["message_question", "message_answer"]
    for extra in ("created_at", "updated_at", "message_no", "msg_id", "message_id", "id"):
        if extra in available:
            select_cols.append(extra)

    fields = sql.SQL(", ").join(sql.Identifier(col) for col in select_cols)
    query = sql.SQL("SELECT {fields} FROM {table} WHERE conv_id = %s").format(
        fields=fields,
        table=sql.Identifier(table_name),
    )
    if "created_at" in available:
        query += sql.SQL(" ORDER BY created_at ASC NULLS LAST")
    elif "updated_at" in available:
        query += sql.SQL(" ORDER BY updated_at ASC NULLS LAST")
    elif "message_no" in available:
        query += sql.SQL(" ORDER BY message_no ASC")
    elif "msg_id" in available:
        query += sql.SQL(" ORDER BY msg_id ASC")
    elif "message_id" in available:
        query += sql.SQL(" ORDER BY message_id ASC")
    elif "id" in available:
        query += sql.SQL(" ORDER BY id ASC")

    with conn.cursor() as cur:
        cur.execute(query, (conv_id,))
        rows = cur.fetchall()

    messages: list[dict[str, str]] = []
    question_idx = select_cols.index("message_question")
    answer_idx = select_cols.index("message_answer")
    for row in rows:
        question = (row[question_idx] or "").strip()
        answer = (row[answer_idx] or "").strip()
        if question:
            messages.append({"role": "user", "content": question})
        if answer:
            messages.append({"role": "assistant", "content": answer})
    return messages


def load_conversation_views(phone: str) -> tuple[list[dict[str, Any]], list[ConversationView], str]:
    conn = with_db_connection()
    if not conn:
        return [], [], ""

    try:
        customers = fetch_customers_by_phone(conn, phone)
        if not customers:
            return [], [], ""

        message_table = resolve_message_table(conn)
        if not message_table:
            return customers, [], ""

        customer_ids = [item["cust_id"] for item in customers]
        conversations = fetch_conversations_for_customers(conn, customer_ids)

        views: list[ConversationView] = []
        for conversation in conversations:
            messages = fetch_messages_for_conversation(conn, message_table, conversation["conv_id"])
            views.append(
                ConversationView(
                    conv_id=conversation["conv_id"],
                    conv_no=conversation["conv_no"],
                    created_at=conversation["created_at"],
                    summary=conversation["summary"],
                    messages=messages,
                )
            )

        return customers, views, message_table
    finally:
        conn.close()


def render_chat_css() -> None:
    st.markdown(
        """
        <style>
            .chat-row {
                display: flex;
                margin: 0.3rem 0;
            }
            .chat-row.user {
                justify-content: flex-end;
            }
            .chat-row.assistant {
                justify-content: flex-start;
            }
            .chat-bubble {
                max-width: 80%;
                border: 1px solid #d9d9d9;
                border-radius: 0.75rem;
                padding: 0.6rem 0.9rem;
                line-height: 1.4;
            }
            .chat-row.user .chat-bubble {
                background: #eaf4ff;
                border-bottom-right-radius: 0.2rem;
            }
            .chat-row.assistant .chat-bubble {
                background: #f8f8f8;
                border-bottom-left-radius: 0.2rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_chat_message(role: str, content: str) -> None:
    alignment = "user" if role == "user" else "assistant"
    st.markdown(
        f'<div class="chat-row {alignment}"><div class="chat-bubble">{content}</div></div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Conversation Dashboard", page_icon="📋", layout="wide")
    st.title("Chatbot Conversation Dashboard")
    st.caption("Read-only internal view of chatbot and customer conversation history.")

    if "search_phone" not in st.session_state:
        st.session_state.search_phone = ""
    if "dashboard_customers" not in st.session_state:
        st.session_state.dashboard_customers = []
    if "dashboard_conversations" not in st.session_state:
        st.session_state.dashboard_conversations = []
    if "dashboard_message_table" not in st.session_state:
        st.session_state.dashboard_message_table = ""

    render_chat_css()

    col_phone, col_btn = st.columns([4, 1])
    with col_phone:
        phone = st.text_input(
            "Search by customer phone number",
            value=st.session_state.search_phone,
            placeholder="Enter cust_phone",
        )
    with col_btn:
        search_clicked = st.button("Search", use_container_width=True)

    if search_clicked:
        clean_phone = phone.strip()
        st.session_state.search_phone = clean_phone
        if not clean_phone:
            st.error("Please enter a phone number.")
            st.session_state.dashboard_customers = []
            st.session_state.dashboard_conversations = []
            st.session_state.dashboard_message_table = ""
        else:
            with st.spinner("Loading conversation history..."):
                customers, conversations, message_table = load_conversation_views(clean_phone)
            st.session_state.dashboard_customers = customers
            st.session_state.dashboard_conversations = conversations
            st.session_state.dashboard_message_table = message_table

    customers = st.session_state.dashboard_customers
    conversations: list[ConversationView] = st.session_state.dashboard_conversations
    message_table = st.session_state.dashboard_message_table

    if st.session_state.search_phone and not customers:
        st.info("No customer found for this phone number.")
        return

    if not customers:
        st.info("Enter a phone number and click Search.")
        return

    st.subheader("Customer records")
    st.dataframe(customers, use_container_width=True, hide_index=True)
    st.caption(f"Message table used: `{message_table or 'not found'}`")

    if not conversations:
        st.warning("No conversation history found for this customer.")
        return

    st.subheader("Conversation history")
    for conversation in conversations:
        conv_title = conversation.conv_no or conversation.conv_id
        created_text = (
            conversation.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if conversation.created_at
            else "Unknown time"
        )
        with st.expander(f"Conversation {conv_title} ({created_text})", expanded=True):
            if conversation.summary:
                st.caption(f"Summary: {conversation.summary}")
            if not conversation.messages:
                st.write("No messages in this conversation.")
                continue
            for message in conversation.messages:
                render_chat_message(message["role"], message["content"])


if __name__ == "__main__":
    main()
