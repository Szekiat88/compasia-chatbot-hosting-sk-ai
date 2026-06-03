"""
Run on the server to verify get_marketplace_order is working against the DB.

Usage:
    python test_marketplace_db.py
    python test_marketplace_db.py CAM7765
"""
from __future__ import annotations

import sys
import logging
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_marketplace_db")

from marketplace_tracker import (
    _get_db_config,
    get_marketplace_order,
    extract_medusa_order_id,
)


def _separator(title: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print('=' * 55)


def test_db_connection() -> bool:
    _separator("1. DB CONNECTION")
    cfg = _get_db_config()
    pwd = cfg.get("password") or ""
    masked = (pwd[:3] + "***" + pwd[-2:]) if len(pwd) > 5 else ("***" if pwd else "(empty)")
    print(f"  host     : {cfg['host']}")
    print(f"  port     : {cfg['port']}")
    print(f"  dbname   : {cfg['dbname']}")
    print(f"  user     : {cfg['user']}")
    print(f"  password : {masked}")
    try:
        conn = psycopg2.connect(**cfg)
        conn.close()
        print("  RESULT   : OK — connected successfully")
        return True
    except Exception as exc:
        print(f"  RESULT   : FAILED — {exc}")
        return False


def test_table_exists() -> bool:
    _separator("2. TABLE EXISTS")
    cfg = _get_db_config()
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = 'ai_chatbot_order_tracking_status'
            """
        )
        count = cur.fetchone()[0]
        conn.close()
        if count:
            print("  RESULT   : OK — table found")
            return True
        else:
            print("  RESULT   : FAILED — table 'ai_chatbot_order_tracking_status' does not exist")
            return False
    except Exception as exc:
        print(f"  RESULT   : FAILED — {exc}")
        return False


def test_sample_rows() -> None:
    _separator("3. SAMPLE ROWS (first 5)")
    cfg = _get_db_config()
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT order_id, order_status, tracking_number
            FROM ai_chatbot_order_tracking_status
            ORDER BY order_id
            LIMIT 5
            """
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            print("  RESULT   : table is empty — no rows found")
            return
        print(f"  {'ORDER ID':<30} {'STATUS':<20} TRACKING")
        print(f"  {'-'*30} {'-'*20} {'-'*15}")
        for r in rows:
            print(f"  {str(r[0]):<30} {str(r[1]):<20} {str(r[2])}")
        print(f"\n  RESULT   : OK — {len(rows)} sample row(s) returned")
    except Exception as exc:
        print(f"  RESULT   : FAILED — {exc}")


def test_order_lookup(order_id: str) -> None:
    _separator(f"4. ORDER LOOKUP — {order_id}")
    order, failure_reason = get_marketplace_order(order_id)
    if order:
        print(f"  order_id        : {order['order_id']}")
        print(f"  order_status    : {order['order_status']}")
        print(f"  tracking_number : {order['tracking_number']}")
        print(f"  tracking_url    : {order['tracking_url']}")
        print(f"  RESULT          : OK — order found")
    else:
        print(f"  RESULT          : NOT FOUND — reason={failure_reason}")


def test_regex(text: str) -> None:
    _separator(f"5. REGEX EXTRACTION — '{text}'")
    result = extract_medusa_order_id(text)
    if result:
        print(f"  extracted : {result}")
        print(f"  RESULT    : OK — order ID detected")
    else:
        print(f"  RESULT    : NO MATCH — order ID not detected from this text")


if __name__ == "__main__":
    order_id_arg = sys.argv[1] if len(sys.argv) > 1 else None

    connected = test_db_connection()
    if not connected:
        print("\nCannot proceed — fix the DB connection first.")
        sys.exit(1)

    table_ok = test_table_exists()
    if not table_ok:
        print("\nCannot proceed — fix the table first.")
        sys.exit(1)

    test_sample_rows()

    if order_id_arg:
        test_order_lookup(order_id_arg)
        test_regex(order_id_arg)
    else:
        print("\n  TIP: pass an order ID to test a specific lookup:")
        print("       python test_marketplace_db.py CAM7765")
