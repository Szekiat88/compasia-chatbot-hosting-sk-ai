"""
Usage:
    python shopify_trackker.py
"""

import os

import requests
from format import format_shopify_order

API_VERSION = "2025-10"
SHOP_DOMAIN = os.getenv("SHOP_DOMAIN", "compasia-malaysia.myshopify.com")
DEFAULT_ORDER_NAME = "#CAM7765"
DEFAULT_PRODUCT_QUERY = "iphone 11"

ORDER_PAYMENT_QUERY = """
query ($q: String!) {
  orders(first: 1, query: $q) {
    edges {
      node {
        id
        name
        totalPrice
        subtotalPrice
        totalTax
        displayFulfillmentStatus
        lineItems(first: 1) {
          edges {
            node {
              title
              quantity
              variant {
                id
                title
                sku
                inventoryQuantity
                inventoryItem {
                  id
                  tracked
                }
              }
            }
          }
        }
        fulfillments {
          id
          status
          trackingInfo {
            number
            url
            company
          }
        }
        transactions(first: 5) {
          id
          kind
          status
          gateway
        }
      }
    }
  }
}
"""

ORDER_DETAILS_QUERY = """
query ($q: String!) {
  orders(first: 1, query: $q) {
    edges {
      node {
        id
        name
        totalPrice
        subtotalPrice
        totalTax
        displayFulfillmentStatus
        lineItems(first: 5) {
          edges {
            node {
              title
              quantity
              variant {
                id
                title
                sku
                inventoryQuantity
                inventoryItem {
                  id
                  tracked
                }
              }
            }
          }
        }
        fulfillments {
          id
          status
          trackingInfo {
            number
            url
            company
          }
        }
      }
    }
  }
}
"""

PRODUCT_SEARCH_QUERY = """
query ($q: String!) {
  products(first: 1, query: $q) {
    edges {
      node {
        id
        title
        variants(first: 10) {
          edges {
            node {
              id
              title
            }
          }
        }
      }
    }
  }
}
"""


def graphql_endpoint() -> str:
    return f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/graphql.json"


def execute(query: str, variables: dict) -> dict:
    access_token = os.getenv("ACCESS_TOKEN", "")
    if not access_token:
        raise RuntimeError("Shopify ACCESS_TOKEN is not configured. Add ACCESS_TOKEN to your .env file.")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    resp = requests.post(graphql_endpoint(), json={"query": query, "variables": variables}, headers=headers, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body.get("data", {})


def normalize_order_name(order_name: str) -> str:
    raw = str(order_name or "").strip().upper().replace(" ", "")
    if not raw:
        return DEFAULT_ORDER_NAME
    if raw.startswith("#"):
        return raw
    if raw.startswith("CAM"):
        return f"#{raw}"
    return raw


def get_payment_detail(order_name: str = DEFAULT_ORDER_NAME) -> str:
    search_query = f'name:"{normalize_order_name(order_name)}"'
    data = execute(ORDER_PAYMENT_QUERY, {"q": search_query})
    order = first_order_node(data)
    return format_order(order)


def get_order_detail(order_name: str = DEFAULT_ORDER_NAME) -> str:
    search_query = f'name:"{normalize_order_name(order_name)}"'
    print("Hello search_query: ", search_query)
    data = execute(ORDER_DETAILS_QUERY, {"q": search_query})
    print("Hello data: ", data)
    order = first_order_node(data)
    return format_order(order)


def find_all_products(search_query: str = DEFAULT_PRODUCT_QUERY) -> str:
    if ":" not in search_query:
        search_query = f'title:"{search_query}"'
    data = execute(PRODUCT_SEARCH_QUERY, {"q": search_query})
    return format_products(data)


def format_order(order: dict) -> str:
    return format_shopify_order(order)


def format_products(products_payload: dict) -> str:
    products = products_payload.get("products", {}).get("edges") or []
    if not products:
        return "No products found."

    lines = []
    lines.append("products:")
    for edge in products:
        node = edge.get("node") or {}
        lines.append(f"  - id: {node.get('id')}")
        lines.append(f"    title: {node.get('title')}")
        variants = node.get("variants", {}).get("edges") or []
        if variants:
            # Group by color + storage, aggregate conditions and quantity.
            grouped = {}
            for v_edge in variants:
                v = v_edge.get("node") or {}
                raw_title = v.get("title") or ""
                parts = [p.strip() for p in raw_title.split("/") if p.strip()]
                color = parts[0] if len(parts) > 0 else ""
                storage = parts[1] if len(parts) > 1 else ""
                condition_raw = parts[2].lower() if len(parts) > 2 else ""
                if "12" in condition_raw:
                    condition = "Fair"
                elif "24" in condition_raw:
                    condition = "Excellent"
                else:
                    condition = parts[2] if len(parts) > 2 else ""
                key = f"{color}|{storage}"
                bucket = grouped.setdefault(
                    key,
                    {"color": color, "storage": storage, "conditions": {}, "ids": [], "total": 0},
                )
                bucket["ids"].append(v.get("id"))
                bucket["conditions"].setdefault(condition, 0)
                bucket["conditions"][condition] += 1
                bucket["total"] += 1

            # Table header
            lines.append("    variants:")
            lines.append("      color | storage | conditions | quantity | ids")
            lines.append("      ------|---------|------------|----------|----")
            for bucket in grouped.values():
                condition_str = ", ".join(
                    f"{name} ({count})" if count > 1 else name for name, count in bucket["conditions"].items()
                )
                ids_str = ", ".join(bucket["ids"])
                lines.append(
                    f"      {bucket['color']} | {bucket['storage']} | {condition_str} | {bucket['total']} | {ids_str}"
                )
    return "\n".join(lines)


def first_order_node(payload: dict) -> dict:
    edges = payload.get("orders", {}).get("edges") or []
    if not edges:
        raise RuntimeError("No order found for query.")
    return edges[0].get("node") or {}


def main() -> None:
    print("Payment detail")
    print(get_payment_detail())
    print("Order detail")
    print(get_order_detail())
    print("Products")
    print(find_all_products())


if __name__ == "__main__":
    main()
