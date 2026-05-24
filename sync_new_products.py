"""
Sync products from the new Medusa.js marketplace DB into the FAISS chatbot DB.

Usage:
    python sync_new_products.py [--dry-run] [--rebuild-index]

What it does:
    1. Connects to marketplace_mercur_uat (new product DB via SSM tunnel :5421)
    2. Extracts all published, non-deleted products + variants with:
         - handle, vendor, product_type, color, spec, condition, price, tenure
         - correct availability flag (respects manage_inventory + allow_backorder)
    3. Upserts into marketplace_variant table in ai-grading-uat (FAISS DB :5431)
    4. Optionally rebuilds the FAISS semantic search index

Availability rules (per user spec):
    - deleted_at IS NULL applied everywhere
    - product.status = 'published' only
    - available_qty = stocked_quantity - reserved_quantity (can be negative)
    - manage_inventory = FALSE → always available (skip stock check)
    - manage_inventory = TRUE + allow_backorder = TRUE → always available
    - manage_inventory = TRUE + allow_backorder = FALSE → only if available_qty > 0

Requires SSM tunnels:
    New DB (port 5421):
        aws ssm start-session --region ap-southeast-5 --target i-046d2ea75fdd7997d
          --document-name AWS-StartPortForwardingSessionToRemoteHost
          --parameters '{"portNumber":["5432"],"localPortNumber":["5421"],
            "host":["my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com"]}'
          --profile marketplace
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# DB configs (read from env files)
# ---------------------------------------------------------------------------
NEW_DB = dict(
    host=os.getenv("NEW_DB_HOST", "localhost"),
    port=int(os.getenv("NEW_DB_PORT", "5421")),
    dbname=os.getenv("NEW_DB_NAME", "marketplace_mercur_uat"),
    user=os.getenv("NEW_DB_USER", "szekiat"),
    password=os.getenv("NEW_DB_PASSWORD", "994NlQ6pK42jN2tx"),
)

FAISS_DB_ENV = "db.env"


# ---------------------------------------------------------------------------
# Extraction SQL — runs on marketplace_mercur_uat
# ---------------------------------------------------------------------------
# Price: latest price per variant (most recent created_at), any currency/list.
# Spec:  all capacity/size/model/tenure/design tags concatenated with " | ".
#        tenure is merged into spec (tenure column in target is left NULL).
# ---------------------------------------------------------------------------
EXTRACT_SQL = """
WITH latest_price AS (
    SELECT
        pvps.variant_id,
        pr.amount,
        pr.currency_code,
        ROW_NUMBER() OVER (
            PARTITION BY pvps.variant_id
            ORDER BY pr.created_at DESC
        ) AS rn
    FROM product_variant_price_set pvps
    INNER JOIN price pr
        ON pr.price_set_id = pvps.price_set_id
       AND pr.deleted_at   IS NULL
    WHERE pvps.deleted_at IS NULL
)
SELECT
    abs(('x' || substr(md5(p.id),  1, 15))::bit(60)::bigint)   AS product_id,
    abs(('x' || substr(md5(pv.id), 1, 15))::bit(60)::bigint)   AS variant_id,

    p.id                                                         AS src_product_id,
    pv.id                                                        AS src_variant_id,
    p.handle,

    COALESCE(
        MAX(CASE WHEN po.title = 'Brand' THEN pov.value END),
        split_part(p.title, ' ', 1)
    )                                                            AS vendor,

    COALESCE(pt.value, 'Unknown')                                AS product_type,

    -- color
    MAX(CASE
        WHEN po.title IN ('Color', 'Colour') THEN pov.value
    END)                                                         AS color,

    -- spec: capacity / size / phone model / tenure / design all merged here
    NULLIF(STRING_AGG(
        CASE
            WHEN po.title IN (
                'Capacity', 'RAM & Storage', 'Storage', 'Size',
                'Phone model', 'Phone Model', 'Model',
                'Tenure', 'Month', 'Design'
            ) THEN pov.value
        END,
        ' | '
        ORDER BY po.title
    ), '')                                                       AS spec,

    -- condition / grade
    MAX(CASE
        WHEN po.title IN (
            'Cosmetic Grading', 'Cosmetic Grade',
            'Device Grading', 'Grade', 'Condition'
        ) THEN pov.value
    END)                                                         AS condition,

    -- latest price (most recent created_at, any currency / price list)
    lp.amount                                                    AS price,

    COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) AS available_qty,

    CASE
        WHEN pv.manage_inventory = FALSE
            THEN TRUE
        WHEN pv.manage_inventory = TRUE AND pv.allow_backorder = TRUE
            THEN TRUE
        WHEN pv.manage_inventory = TRUE AND pv.allow_backorder = FALSE
             AND COALESCE(SUM(il.stocked_quantity - il.reserved_quantity), 0) > 0
            THEN TRUE
        ELSE FALSE
    END                                                          AS is_available

FROM product p

JOIN product_variant pv
    ON pv.product_id  = p.id
   AND pv.deleted_at  IS NULL

LEFT JOIN product_type pt
    ON pt.id          = p.type_id
   AND pt.deleted_at  IS NULL

LEFT JOIN product_variant_option pvo
    ON pvo.variant_id = pv.id

LEFT JOIN product_option_value pov
    ON pov.id         = pvo.option_value_id
   AND pov.deleted_at IS NULL

LEFT JOIN product_option po
    ON po.id          = pov.option_id
   AND po.deleted_at  IS NULL

LEFT JOIN product_variant_inventory_item pvii
    ON pvii.variant_id = pv.id
   AND pvii.deleted_at IS NULL

LEFT JOIN inventory_level il
    ON il.inventory_item_id = pvii.inventory_item_id
   AND il.deleted_at        IS NULL

INNER JOIN latest_price lp
    ON lp.variant_id = pv.id
   AND lp.rn         = 1

WHERE p.deleted_at IS NULL
  AND p.status      = 'published'
  AND (
      pv.manage_inventory = FALSE
      OR (
          pv.manage_inventory = TRUE
          AND COALESCE(il.stocked_quantity - il.reserved_quantity, 0) > 0
      )
      OR (
          pv.manage_inventory = TRUE
          AND pv.allow_backorder = TRUE
          AND COALESCE(il.stocked_quantity - il.reserved_quantity, 0) <= 0
      )
  )

GROUP BY
    p.id, pv.id, p.handle, pt.value,
    pv.manage_inventory, pv.allow_backorder,
    lp.amount

ORDER BY p.handle, pv.id
"""

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS marketplace_variant (
    product_id      BIGINT NOT NULL,
    variant_id      BIGINT NOT NULL,
    src_product_id  TEXT,
    src_variant_id  TEXT,
    handle          TEXT,
    vendor          TEXT,
    product_type    TEXT,
    color           TEXT,
    spec            TEXT,
    condition       TEXT,
    price           NUMERIC(12,2),
    tenure          TEXT,
    available_qty   INTEGER     DEFAULT 0,
    is_available    BOOLEAN     DEFAULT FALSE,
    synced_at       TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (product_id, variant_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS marketplace_variant_src_idx
    ON marketplace_variant (src_variant_id)
    WHERE src_variant_id IS NOT NULL;
"""

UPSERT_SQL = """
INSERT INTO marketplace_variant (
    product_id, variant_id,
    src_product_id, src_variant_id,
    handle, vendor, product_type,
    color, spec, condition, price, tenure,
    available_qty, is_available
)
VALUES %s
ON CONFLICT (product_id, variant_id) DO UPDATE SET
    src_product_id  = EXCLUDED.src_product_id,
    src_variant_id  = EXCLUDED.src_variant_id,
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


def load_env_file(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def get_new_db_conn():
    try:
        return psycopg2.connect(**NEW_DB, connect_timeout=10)
    except psycopg2.OperationalError as e:
        print(f"\n[ERROR] Cannot connect to marketplace DB: {e}")
        print("  Make sure the SSM tunnel is running on port 5421.")
        print("  Command:")
        print("    aws ssm start-session --region ap-southeast-5 \\")
        print("      --target i-046d2ea75fdd7997d \\")
        print("      --document-name AWS-StartPortForwardingSessionToRemoteHost \\")
        print("      --parameters '{\"portNumber\":[\"5432\"],\"localPortNumber\":[\"5421\"],")
        print("        \"host\":[\"my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com\"]}' \\")
        print("      --profile marketplace")
        sys.exit(1)


def get_faiss_db_conn():
    env = load_env_file(FAISS_DB_ENV)
    try:
        return psycopg2.connect(
            host=env.get("DB_HOST", "127.0.0.1"),
            port=int(env.get("DB_PORT", "5431")),
            dbname=env.get("DB_NAME", "ai-grading-uat"),
            user=env.get("DB_USER", ""),
            password=env.get("DB_PASSWORD", ""),
            connect_timeout=10,
        )
    except psycopg2.OperationalError as e:
        print(f"\n[ERROR] Cannot connect to FAISS DB (ai-grading-uat): {e}")
        print("  Make sure your local DB or SSM tunnel to port 5431 is active.")
        sys.exit(1)


def extract_rows(conn) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(EXTRACT_SQL)
        return [dict(r) for r in cur.fetchall()]


def to_tuple(r: Dict[str, Any]):
    def _dec(v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    def _int(v: Any) -> Optional[int]:
        if v is None:
            return 0
        try:
            return int(v)
        except Exception:
            return 0

    return (
        int(r["product_id"]),
        int(r["variant_id"]),
        str(r.get("src_product_id") or ""),
        str(r.get("src_variant_id") or ""),
        str(r.get("handle") or ""),
        str(r.get("vendor") or ""),
        str(r.get("product_type") or ""),
        str(r.get("color") or "") or None,
        str(r.get("spec") or "") or None,   # includes capacity/size/model/tenure/design
        str(r.get("condition") or "") or None,
        _dec(r.get("price")),
        None,                                # tenure merged into spec; column left NULL
        _int(r.get("available_qty")),
        bool(r.get("is_available", False)),
    )


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(ENSURE_TABLE_SQL)
    conn.commit()


def upsert_rows(conn, rows: List[Dict[str, Any]]) -> int:
    tuples = [to_tuple(r) for r in rows]
    with conn.cursor() as cur:
        execute_values(cur, UPSERT_SQL, tuples, page_size=500)
    conn.commit()
    return len(tuples)


def print_preview(rows: List[Dict[str, Any]], limit: int = 10) -> None:
    avail = sum(1 for r in rows if r.get("is_available"))
    print(f"\n  Total rows: {len(rows)} | Available variants: {avail}")
    print(f"\n  {'handle':<30} {'type':<16} {'vendor':<12} "
          f"{'color':<10} {'spec':<10} {'cond':<10} {'price':>8} {'avail':>6}")
    print(f"  {'-'*108}")
    for r in rows[:limit]:
        print(
            f"  {str(r.get('handle',''))[:28]:<30} "
            f"{str(r.get('product_type',''))[:14]:<16} "
            f"{str(r.get('vendor',''))[:10]:<12} "
            f"{str(r.get('color') or '')[:8]:<10} "
            f"{str(r.get('spec') or '')[:8]:<10} "
            f"{str(r.get('condition') or '')[:8]:<10} "
            f"{str(r.get('price') or ''):>8} "
            f"{'YES' if r.get('is_available') else 'no':>6}"
        )
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more rows")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync marketplace products to FAISS DB")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview rows without writing to FAISS DB")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="Rebuild FAISS index after syncing")
    args = parser.parse_args()

    print("=" * 60)
    print("  Marketplace → FAISS DB Product Sync")
    print("=" * 60)

    print("\n[1/3] Connecting to marketplace DB (port 5421)...")
    new_conn = get_new_db_conn()
    print("  Connected.")

    print("\n[2/3] Extracting products from marketplace DB...")
    rows = extract_rows(new_conn)
    new_conn.close()
    print(f"  Extracted {len(rows)} variant rows.")

    if not rows:
        print("  No published products found.")
        return 0

    print_preview(rows, limit=15)

    if args.dry_run:
        print("\n  [DRY RUN] Nothing written. Remove --dry-run to sync.")
        return 0

    print("\n[3/3] Upserting into marketplace_variant (FAISS DB @ port 5431)...")
    faiss_conn = get_faiss_db_conn()
    ensure_table(faiss_conn)
    count = upsert_rows(faiss_conn, rows)
    faiss_conn.close()
    print(f"  Upserted {count} rows into marketplace_variant.")

    if args.rebuild_index:
        print("\n[BONUS] Rebuilding FAISS semantic search index...")
        result = subprocess.run([sys.executable, "build_vectors.py"])
        if result.returncode == 0:
            print("  Index rebuilt successfully.")
        else:
            print("  Index rebuild failed — run build_vectors.py manually.")

    print("\nDone. Run sync_products.py to push the updated index to Railway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
