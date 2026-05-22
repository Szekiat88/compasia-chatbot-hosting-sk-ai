"""Shared DB logging handler — attach to root logger at app startup."""
from __future__ import annotations

import logging
import sys
import threading

import psycopg2

from conversation_ids import _get_db_config

_TABLE_READY = False
_TABLE_LOCK = threading.Lock()


def _ensure_log_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    with _TABLE_LOCK:
        if _TABLE_READY:
            return
        try:
            conn = psycopg2.connect(**_get_db_config())
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS webhook_log (
                            id          BIGSERIAL PRIMARY KEY,
                            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            level       TEXT NOT NULL,
                            logger_name TEXT NOT NULL,
                            sender      TEXT,
                            message     TEXT NOT NULL,
                            exc_text    TEXT
                        )
                        """
                    )
            conn.close()
            _TABLE_READY = True
        except Exception as exc:
            print(f"[db_log_handler] could not create webhook_log table: {exc}", file=sys.stderr)


class DBLogHandler(logging.Handler):
    """Writes every log record to the webhook_log table in PostgreSQL."""

    # thread-local sender set by the webhook per request
    _context = threading.local()

    @classmethod
    def set_sender(cls, phone: str | None) -> None:
        cls._context.sender = phone

    @classmethod
    def clear_sender(cls) -> None:
        cls._context.sender = None

    def emit(self, record: logging.LogRecord) -> None:
        _ensure_log_table()
        if not _TABLE_READY:
            return
        sender = getattr(self._context, "sender", None)
        try:
            conn = psycopg2.connect(**_get_db_config())
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO webhook_log (level, logger_name, sender, message, exc_text)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            record.levelname,
                            record.name,
                            sender,
                            self.format(record),
                            logging.Formatter().formatException(record.exc_info) if record.exc_info else None,
                        ),
                    )
            conn.close()
        except Exception:
            pass  # silently skip — DB logging is best-effort


_handler: DBLogHandler | None = None


def setup_db_logging(level: int = logging.INFO) -> None:
    """Call once at app startup to attach DB logging to the root logger."""
    global _handler
    if _handler is not None:
        return
    _handler = DBLogHandler(level=level)
    logging.getLogger().addHandler(_handler)
