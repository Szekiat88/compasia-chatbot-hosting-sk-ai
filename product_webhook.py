"""
Medusa.js product-update webhook → marketplace_variant upsert.

Endpoint: POST /marketplace/product-updated
Security: HMAC-SHA256 over raw request body using MEDUSA_WEBHOOK_SECRET,
          delivered via `x-medusa-signature` header.

Set MEDUSA_WEBHOOK_SECRET in your environment (or .env).
If the env var is absent, signature verification is skipped and a warning is
logged — set it in production.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import subprocess
import sys
import threading
from decimal import Decimal
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from flask import Blueprint, jsonify, request

logger = logging.getLogger("product_webhook")

marketplace_bp = Blueprint("marketplace", __name__, url_prefix="/marketplace")

# ---------------------------------------------------------------------------
# Option-title buckets (mirrors sync_new_products.py EXTRACT_SQL)
# ---------------------------------------------------------------------------
_SPEC_TITLES = {
    "Capacity", "RAM & Storage", "Storage", "Size",
    "Phone model", "Phone Model", "Model",
    "Tenure", "Month", "Design",
}
_COLOR_TITLES = {"Color", "Colour"}
_CONDITION_TITLES = {
    "Cosmetic Grading", "Cosmetic Grade",
    "Device Grading", "Grade", "Condition",
}
_BRAND_TITLES = {"Brand"}


# ---------------------------------------------------------------------------
# ID hashing — replicates PostgreSQL:
#   abs(('x' || substr(md5(id), 1, 15))::bit(60)::bigint)
# ---------------------------------------------------------------------------
def _medusa_id_to_int(src_id: str) -> int:
    hex15 = hashlib.md5(src_id.encode()).hexdigest()[:15]  # 15 hex = 60 bits
    val = int(hex15, 16)
    if val >= (1 << 59):          # treat as signed 60-bit
        val -= (1 << 60)
    return abs(val)


# ---------------------------------------------------------------------------
# FAISS DB connection (reads db.env)
# ---------------------------------------------------------------------------
def _load_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _get_faiss_conn():
    env = _load_env_file(
        os.path.join(os.path.dirname(__file__), "db.env")
    )
    return psycopg2.connect(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "5431")),
        dbname=env.get("DB_NAME", "ai-grading-uat"),
        user=env.get("DB_USER", ""),
        password=env.get("DB_PASSWORD", ""),
        connect_timeout=10,
    )


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------
def _parse_product(product: dict[str, Any]) -> list[tuple]:
    """Return a list of upsert tuples, one per active variant."""
    src_product_id: str = product.get("id", "")
    handle: str = product.get("handle", "")
    status: str = product.get("status", "")
    product_deleted: bool = product.get("deleted_at") is not None

    ptype_obj = product.get("type") or {}
    product_type: str = ptype_obj.get("value", "Unknown") if isinstance(ptype_obj, dict) else "Unknown"

    # Fallback vendor = first word of title
    default_vendor = (product.get("title") or "").split()[0] if product.get("title") else ""

    rows: list[tuple] = []

    for variant in product.get("variants") or []:
        if variant.get("deleted_at") is not None:
            continue

        src_variant_id: str = variant.get("id", "")
        manage_inv: bool = bool(variant.get("manage_inventory", True))
        allow_backorder: bool = bool(variant.get("allow_backorder", False))

        # --- options ---
        vendor = default_vendor
        color: str | None = None
        condition: str | None = None
        spec_parts: list[str] = []

        for opt_entry in variant.get("options") or []:
            opt = opt_entry.get("option") or {}
            optval = opt_entry.get("option_value") or {}
            if opt.get("deleted_at") or optval.get("deleted_at"):
                continue
            title: str = opt.get("title", "")
            value: str = optval.get("value", "")
            if title in _BRAND_TITLES:
                vendor = value
            elif title in _COLOR_TITLES:
                color = value
            elif title in _CONDITION_TITLES:
                condition = value
            elif title in _SPEC_TITLES:
                spec_parts.append(value)

        spec: str | None = " | ".join(sorted(spec_parts)) or None

        # --- inventory ---
        total_stocked = 0
        total_reserved = 0
        for inv_item in variant.get("inventory_items") or []:
            if inv_item.get("deleted_at"):
                continue
            level = inv_item.get("inventory_level") or {}
            if level.get("deleted_at"):
                continue
            total_stocked += int(level.get("stocked_quantity", 0) or 0)
            total_reserved += int(level.get("reserved_quantity", 0) or 0)
        available_qty = total_stocked - total_reserved

        # --- availability ---
        if product_deleted or status != "published":
            is_available = False
        elif not manage_inv:
            is_available = True
        elif manage_inv and allow_backorder:
            is_available = True
        else:
            is_available = available_qty > 0

        # --- price: latest created_at across all price_sets ---
        best_price: Decimal | None = None
        best_ts: str | None = None
        for ps_entry in variant.get("price_sets") or []:
            if ps_entry.get("deleted_at"):
                continue
            for price in ps_entry.get("prices") or []:
                if price.get("deleted_at"):
                    continue
                ts = price.get("created_at") or ""
                if best_ts is None or ts > best_ts:
                    best_ts = ts
                    try:
                        best_price = Decimal(str(price["amount"])) / 100
                    except Exception:
                        best_price = None

        rows.append((
            _medusa_id_to_int(src_product_id),
            _medusa_id_to_int(src_variant_id),
            src_product_id,
            src_variant_id,
            handle,
            vendor or None,
            product_type,
            color,
            spec,
            condition,
            best_price,
            None,            # tenure merged into spec
            available_qty,
            is_available,
        ))

    return rows


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = """
INSERT INTO marketplace_variant (
    product_id, variant_id,
    src_product_id, src_variant_id,
    handle, vendor, product_type,
    color, spec, condition, price, tenure,
    available_qty, is_available
)
VALUES %s
ON CONFLICT (src_variant_id) WHERE src_variant_id IS NOT NULL DO UPDATE SET
    product_id      = EXCLUDED.product_id,
    src_product_id  = EXCLUDED.src_product_id,
    handle          = EXCLUDED.handle,
    vendor          = EXCLUDED.vendor,
    product_type    = EXCLUDED.product_type,
    color           = EXCLUDED.color,
    spec            = EXCLUDED.spec,
    condition       = EXCLUDED.condition,
    price           = EXCLUDED.price,
    tenure          = EXCLUDED.tenure,
    available_qty   = EXCLUDED.available_qty,
    is_available    = EXCLUDED.is_available,
    synced_at       = now()
"""


def _upsert(rows: list[tuple]) -> int:
    conn = _get_faiss_conn()
    try:
        with conn.cursor() as cur:
            execute_values(cur, _UPSERT_SQL, rows, page_size=500)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def _rebuild_index_async() -> None:
    def _run():
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "build_vectors.py")],
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info("product_webhook faiss_rebuild=ok")
        else:
            logger.error(
                "product_webhook faiss_rebuild=failed stderr=%s",
                result.stderr.decode(errors="replace")[:500],
            )

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------
def _verify_signature(raw_body: bytes) -> bool:
    secret = os.getenv("MEDUSA_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("product_webhook MEDUSA_WEBHOOK_SECRET not set — skipping signature check")
        return True

    received = request.headers.get("x-medusa-signature", "")
    if not received:
        logger.warning("product_webhook missing x-medusa-signature header")
        return False

    expected = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@marketplace_bp.route("/product-updated", methods=["POST"])
def product_updated():
    raw_body = request.get_data()

    if not _verify_signature(raw_body):
        logger.warning("product_webhook signature_mismatch remote=%s", request.remote_addr)
        return jsonify({"error": "invalid signature"}), 401

    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict) or "product" not in payload:
        return jsonify({"error": "missing product key"}), 400

    product = payload["product"]
    src_product_id = product.get("id", "<unknown>")

    logger.info(
        "product_webhook received product_id=%s handle=%s status=%s",
        src_product_id,
        product.get("handle"),
        product.get("status"),
    )

    try:
        rows = _parse_product(product)
    except Exception as e:
        logger.exception("product_webhook parse_error product_id=%s", src_product_id)
        return jsonify({"error": "payload parse error", "detail": str(e)}), 422

    if not rows:
        logger.info("product_webhook no_active_variants product_id=%s", src_product_id)
        return jsonify({"status": "ok", "upserted": 0}), 200

    try:
        count = _upsert(rows)
    except Exception as e:
        logger.exception("product_webhook db_error product_id=%s", src_product_id)
        return jsonify({"error": "database error", "detail": str(e)}), 500

    logger.info("product_webhook upserted=%d product_id=%s", count, src_product_id)

    if os.getenv("PRODUCT_WEBHOOK_REBUILD_INDEX", "0") == "1":
        _rebuild_index_async()

    return jsonify({"status": "ok", "upserted": count}), 200
