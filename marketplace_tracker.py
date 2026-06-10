"""
Query order tracking status from the marketplace_mercur_uat database.

Requires the SSM tunnel on port 5421:
    aws ssm start-session --region ap-southeast-5 --target i-046d2ea75fdd7997d
      --document-name AWS-StartPortForwardingSessionToRemoteHost
      --parameters '{"portNumber":["5432"],"localPortNumber":["5421"],
        "host":["my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com"]}'
      --profile marketplace
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import psycopg2

logger = logging.getLogger("marketplace_tracker")

BASE_DIR = Path(__file__).parent
_ENV_FILE = BASE_DIR / "db_new.env"


# CAM order ID pattern: must start with CAMY (e.g. CAMY575, CAMY1234).
_CAM_ORDER_RE = re.compile(r"(?<![A-Z0-9])#?(CAMY[0-9A-Z-]*\d[0-9A-Z-]*)\b", re.IGNORECASE)


def _load_env_file() -> dict[str, str]:
    data: dict[str, str] = {}
    if not _ENV_FILE.exists():
        return data
    for raw in _ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _get_db_config() -> dict:
    env = _load_env_file()
    return dict(
        host=os.getenv("NEW_DB_HOST") or env.get("NEW_DB_HOST"),
        port=int(os.getenv("NEW_DB_PORT") or env.get("NEW_DB_PORT")),
        dbname=os.getenv("NEW_DB_NAME") or env.get("NEW_DB_NAME", "marketplace_mercur_uat"),
        user=os.getenv("NEW_DB_USER") or env.get("NEW_DB_USER", "ai_chatbot_app"),
        password=os.getenv("NEW_DB_PASSWORD") or env.get("NEW_DB_PASSWORD", ""),
        connect_timeout=8,
    )


def extract_medusa_order_id(text: str) -> str | None:
    """Return the first Medusa order ID (order_XXXX) found in text, or None."""
    match = _MEDUSA_ORDER_RE.search(str(text))
    return match.group(0) if match else None


def extract_cam_order_id(text: str) -> str | None:
    """Return the first CAM order ID (e.g. CAMY575, CAM1234) found in text, or None."""
    match = _CAM_ORDER_RE.search(str(text))
    return match.group(1).upper() if match else None


def get_marketplace_order(order_id: str) -> dict | None:
    """
    Fetch one row from ai_chatbot_order_tracking_status by order_id.
    Returns a dict with keys: order_id, tracking_number, tracking_url, order_status.
    Returns None if not found or DB unreachable.
    """
    cfg = _get_db_config()
    logger.info("marketplace_order_lookup order_id=%s host=%s port=%s db=%s user=%s",
                order_id, cfg["host"], cfg["port"], cfg["dbname"], cfg["user"])
    try:
        conn = psycopg2.connect(**cfg)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT custom_display_id, tracking_number, tracking_url, order_status
            FROM ai_chatbot_order_tracking_status
            WHERE custom_display_id = %s
            LIMIT 1
            """,
            (order_id,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            logger.warning("marketplace_order_not_found order_id=%s", order_id)
            return None
        logger.info("marketplace_order_found order_id=%s status=%s", order_id, row[3])
        return {
            "order_id": row[0],
            "tracking_number": row[1],
            "tracking_url": row[2],
            "order_status": row[3],
        }
    except Exception as exc:
        logger.error("marketplace_order_db_error order_id=%s error=%s", order_id, exc)
        return None


def format_marketplace_order(order: dict | None) -> str:
    """Render a marketplace order row into a chat-friendly reply."""
    if not order:
        return "I couldn't find that order in our system. Please double-check your order ID and try again."

    def _v(val: object) -> str:
        s = str(val).strip() if val else ""
        return s if s else "-"

    tracking_number = _v(order.get("tracking_number"))
    tracking_url = _v(order.get("tracking_url"))
    order_status = _v(order.get("order_status"))
    order_id = _v(order.get("order_id"))

    lines = [
        f"Order {order_id}",
        "",
        f"- Order Status: {order_status}",
        f"- Tracking Number: {tracking_number}",
    ]
    if tracking_url != "-":
        lines.append(f"- Track your order: {tracking_url}")

    return "\n".join(lines)


def get_marketplace_order_detail(order_id: str) -> str:
    """Convenience wrapper used by chat_services — returns a formatted string."""
    order = get_marketplace_order(order_id)
    return format_marketplace_order(order)


def get_all_orders() -> list[dict]:
    """Fetch all rows from ai_chatbot_order_tracking_status."""
    try:
        conn = psycopg2.connect(**_get_db_config())
        cur = conn.cursor()
        cur.execute(
            "SELECT order_id, tracking_number, tracking_url, order_status "
            "FROM ai_chatbot_order_tracking_status "
            "ORDER BY order_id"
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {
                "order_id": r[0],
                "tracking_number": r[1],
                "tracking_url": r[2],
                "order_status": r[3],
            }
            for r in rows
        ]
    except Exception as exc:
        print(f"[marketplace_tracker] DB error: {exc}")
        return []


def main() -> None:
    orders = get_all_orders()
    if not orders:
        print("No orders found or DB unreachable.")
        return

    print(f"{'ORDER ID':<40} {'STATUS':<20} {'TRACKING NO':<20} {'TRACKING URL'}")
    print("-" * 110)
    for o in orders:
        oid = o["order_id"] or "-"
        status = o["order_status"] or "-"
        tracking = o["tracking_number"] or "-"
        url = o["tracking_url"] or "-"
        print(f"{oid:<40} {status:<20} {tracking:<20} {url}")

    print(f"\nTotal: {len(orders)} orders")


if __name__ == "__main__":
    main()
