"""Sync Shopify product/variant stock into Postgres."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

from shopify_stock import ShopifyStockAll, extract_variant_id, normalize_domain


PRODUCTS_QUERY = """
query ($cursor: String, $pageSize: Int!, $variantSize: Int!) {
  products(first: $pageSize, after: $cursor) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        status
        handle
        totalInventory
        onlineStoreUrl
        vendor
        productType
        createdAt
        updatedAt
        publishedAt
        variants(first: $variantSize) {
          edges {
            node {
              id
              title
              sku
              barcode
              price
              compareAtPrice
              availableForSale
              inventoryQuantity
              createdAt
              updatedAt
            }
          }
        }
      }
    }
  }
}
"""


def extract_product_id(product_gid: str) -> str:
    return product_gid.split("/")[-1] if product_gid else ""


def load_db_env(base_dir: str) -> dict[str, str]:
    env_path = os.path.join(base_dir, "db.env")
    if not os.path.exists(env_path):
        return {}
    data: dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle.read().splitlines():
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
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env = load_db_env(base_dir)
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
        raise RuntimeError(f"Missing DB settings: {', '.join(missing)}")
    return psycopg2.connect(**config)


def get_table_columns(conn, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        return {row[0] for row in cur.fetchall()}

def _pick_column(columns: set[str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _set_if_exists(record: Dict[str, Any], columns: set[str], candidates: Iterable[str], value: Any) -> None:
    for candidate in candidates:
        if candidate in columns:
            record[candidate] = value
            return


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def fetch_all_products(bot: ShopifyStockAll) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    cursor = None
    while True:
        variables = {
            "cursor": cursor,
            "pageSize": 100,
            "variantSize": 100,
        }
        data = bot._post(PRODUCTS_QUERY, variables)
        product_data = data.get("products", {})
        edges = product_data.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            products.append(node)
        page_info = product_data.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    return products


def build_product_link(shop_domain: str, product: Dict[str, Any]) -> str:
    if product.get("onlineStoreUrl"):
        return product["onlineStoreUrl"]
    handle = product.get("handle") or ""
    return f"https://{shop_domain}/products/{handle}"


def upsert_rows(conn, table_name: str, rows: Iterable[Dict[str, Any]], key_columns: List[str]) -> int:
    rows_list = list(rows)
    if not rows_list:
        return 0

    columns = list(rows_list[0].keys())
    assignments = [sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(col), sql.Identifier(col)) for col in columns]

    query = sql.SQL(
        "INSERT INTO {table} ({fields}) VALUES %s "
        "ON CONFLICT ({keys}) DO UPDATE SET {updates}"
    ).format(
        table=sql.Identifier(table_name),
        fields=sql.SQL(", ").join(map(sql.Identifier, columns)),
        keys=sql.SQL(", ").join(map(sql.Identifier, key_columns)),
        updates=sql.SQL(", ").join(assignments),
    )

    values = [[row[col] for col in columns] for row in rows_list]
    with conn.cursor() as cur:
        execute_values(cur, query.as_string(conn), values, page_size=500)
    return len(rows_list)


def sync_shopify_stock() -> None:
    bot = ShopifyStockAll()
    env = load_db_env(os.path.dirname(os.path.abspath(__file__)))
    shop_domain = normalize_domain(
        os.getenv("SHOP_PUBLIC_DOMAIN")
        or os.getenv("SHOP_DOMAIN")
        or env.get("SHOP_PUBLIC_DOMAIN")
        or env.get("SHOP_DOMAIN")
        or bot.shop_domain
    )
    products = fetch_all_products(bot)

    with with_db_connection() as conn:
        product_columns = get_table_columns(conn, "shopify_product")
        variant_columns = get_table_columns(conn, "shopify_variant")

        product_rows: List[Dict[str, Any]] = []
        variant_rows: List[Dict[str, Any]] = []

        product_id_col = _pick_column(product_columns, "product_id")
        variant_product_id_col = _pick_column(variant_columns, "product_id")
        variant_id_col = _pick_column(variant_columns, "variant_id")
        if not product_id_col or not variant_product_id_col or not variant_id_col:
            raise RuntimeError("Missing product_id/variant_id columns in shopify tables.")

        for product in products:
            product_id = extract_product_id(product.get("id") or "")
            if not product_id:
                continue

            product_link = build_product_link(shop_domain, product)
            product_record: Dict[str, Any] = {product_id_col: int(product_id)}
            _set_if_exists(product_record, product_columns, ("title", "product_title", "name"), product.get("title"))
            _set_if_exists(product_record, product_columns, ("handle", "product_handle"), product.get("handle"))
            _set_if_exists(product_record, product_columns, ("status",), product.get("status"))
            _set_if_exists(
                product_record,
                product_columns,
                ("total_inventory", "totalInventory", "inventory_total"),
                product.get("totalInventory"),
            )
            _set_if_exists(
                product_record,
                product_columns,
                ("online_store_url", "product_link", "product_url"),
                product.get("onlineStoreUrl") or product_link,
            )
            _set_if_exists(product_record, product_columns, ("vendor",), product.get("vendor"))
            _set_if_exists(product_record, product_columns, ("product_type", "type"), product.get("productType"))
            _set_if_exists(product_record, product_columns, ("created_at",), product.get("createdAt"))
            _set_if_exists(product_record, product_columns, ("updated_at",), product.get("updatedAt"))
            _set_if_exists(product_record, product_columns, ("published_at",), product.get("publishedAt"))
            _set_if_exists(product_record, product_columns, ("last_synced_at",), datetime.utcnow())
            product_rows.append(product_record)

            variants_payload = product.get("variants", {}).get("edges") or []
            for v_edge in variants_payload:
                variant = v_edge.get("node") or {}
                variant_id = extract_variant_id(variant.get("id") or "")
                if not variant_id:
                    continue
                available = bool(variant.get("availableForSale"))
                inventory_quantity = variant.get("inventoryQuantity") or 0
                variant_record: Dict[str, Any] = {
                    variant_id_col: int(variant_id),
                    variant_product_id_col: int(product_id),
                }
                _set_if_exists(variant_record, variant_columns, ("title", "variant_title"), variant.get("title"))
                _set_if_exists(variant_record, variant_columns, ("sku",), variant.get("sku"))
                _set_if_exists(variant_record, variant_columns, ("barcode",), variant.get("barcode"))
                _set_if_exists(variant_record, variant_columns, ("price",), to_decimal(variant.get("price")))
                _set_if_exists(
                    variant_record,
                    variant_columns,
                    ("compare_at_price", "compareAtPrice"),
                    to_decimal(variant.get("compareAtPrice")),
                )
                _set_if_exists(variant_record, variant_columns, ("available", "available_for_sale"), available)
                _set_if_exists(
                    variant_record,
                    variant_columns,
                    ("inventory_quantity", "inventory", "stock", "quantity"),
                    inventory_quantity,
                )
                _set_if_exists(variant_record, variant_columns, ("created_at",), variant.get("createdAt"))
                _set_if_exists(variant_record, variant_columns, ("updated_at",), variant.get("updatedAt"))
                _set_if_exists(variant_record, variant_columns, ("last_synced_at",), datetime.utcnow())
                variant_rows.append(variant_record)

        if product_rows:
            upsert_rows(conn, "shopify_product", product_rows, ["product_id"])
        if variant_rows:
            upsert_rows(conn, "shopify_variant", variant_rows, ["variant_id"])
        conn.commit()


def main() -> None:
    sync_shopify_stock()
    print("Shopify stock sync completed.")


if __name__ == "__main__":
    main()
