from __future__ import annotations

import os
from pathlib import Path

import psycopg2

PAD_WIDTH = 6


def _load_db_env() -> dict[str, str]:
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


def _get_db_config() -> dict[str, str]:
    env = _load_db_env()
    return {
        "host": os.getenv("DB_HOST") or env.get("DB_HOST", ""),
        "port": os.getenv("DB_PORT") or env.get("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME") or env.get("DB_NAME", ""),
        "user": os.getenv("DB_USER") or env.get("DB_USER", ""),
        "password": os.getenv("DB_PASSWORD") or env.get("DB_PASSWORD", ""),
    }


def _next_sequence(sequence_name: str) -> int:
    config = _get_db_config()
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise RuntimeError(f"Missing DB settings: {', '.join(missing)}")
    conn = psycopg2.connect(**config)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT nextval(%s)", (sequence_name,))
                return int(cur.fetchone()[0])
    finally:
        conn.close()


def _format(prefix: str, seq_value: int) -> str:
    return f"{prefix}-{seq_value:0{PAD_WIDTH}d}"


def customer_no() -> str:
    return _format("CUST-DETAILS", _next_sequence("customer_no_seq"))


def conversation_no() -> str:
    return _format("CONV-SUMMARY", _next_sequence("conversation_no_seq"))


def message_no() -> str:
    return _format("MSG-QNA", _next_sequence("message_no_seq"))
