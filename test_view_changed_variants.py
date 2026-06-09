"""
Verify that vw_changed_variants_ai_chatbot exists on the chatbot DB
and returns sensible data.

Usage:
    python test_view_changed_variants.py
"""

from __future__ import annotations

import sys

import psycopg2

from marketplace_tracker import _get_db_config

VIEW = "vw_changed_variants_ai_chatbot"


def _sep(title: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print("=" * 55)


def test_db_connection() -> bool:
    _sep("1. DB CONNECTION")
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


def test_view_exists() -> bool:
    _sep(f"2. VIEW EXISTS  ({VIEW})")
    cfg = _get_db_config()
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'public'
              AND table_name   = %s
            """,
            (VIEW,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            print(f"  RESULT   : OK — view '{VIEW}' found")
            return True
        else:
            print(f"  RESULT   : FAILED — view '{VIEW}' NOT found")
            print()
            print("  Run deploy/create_changed_view.sql against this DB to create it.")
            return False
    except Exception as exc:
        print(f"  RESULT   : FAILED — {exc}")
        return False


def test_view_count() -> bool:
    _sep("3. ROW COUNT")
    cfg = _get_db_config()
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {VIEW}")
        count = cur.fetchone()[0]
        conn.close()
        print(f"  changed_variants_last_12h : {count}")
        print(f"  RESULT   : OK — query executed successfully")
        if count == 0:
            print("  NOTE     : 0 rows is normal outside a 12-hour change window.")
        return True
    except Exception as exc:
        print(f"  RESULT   : FAILED — {exc}")
        return False


def test_sample_rows(limit: int = 5) -> None:
    _sep(f"4. SAMPLE ROWS  (first {limit})")
    cfg = _get_db_config()
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute(f"SELECT src_product_id, src_variant_id FROM {VIEW} LIMIT %s", (limit,))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            print("  No rows — nothing changed in the last 12 hours.")
            return
        print(f"  {'SRC_PRODUCT_ID':<38} SRC_VARIANT_ID")
        print(f"  {'-'*38} {'-'*38}")
        for r in rows:
            print(f"  {str(r[0]):<38} {str(r[1])}")
        print(f"\n  RESULT   : OK — {len(rows)} row(s) shown")
    except Exception as exc:
        print(f"  RESULT   : FAILED — {exc}")


if __name__ == "__main__":
    connected = test_db_connection()
    if not connected:
        print("\nCannot proceed — fix the DB connection first.")
        sys.exit(1)

    view_ok = test_view_exists()
    if not view_ok:
        print("\nCannot proceed — create the view first.")
        sys.exit(1)

    test_view_count()
    test_sample_rows()

    print(f"\n{'=' * 55}")
    print("  All checks passed.")
    print("=" * 55)
