"""Fetch all Shopify products and show variant availability (no device filtering)."""

import os
from typing import Any, Dict, List, Optional

import requests

# Hard-coded defaults with env overrides.
SHOP_DOMAIN = os.getenv("SHOP_DOMAIN", "compasia-malaysia.myshopify.com")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
API_VERSION = "2025-10"
PRODUCTS_PAGE_SIZE = 100
VARIANTS_PAGE_SIZE = 100

PRODUCT_QUERY = """
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
        variants(first: $variantSize) {
          edges {
            node {
              id
              title
              price
              availableForSale
              inventoryQuantity
            }
          }
        }
      }
    }
  }
}
"""


def graphql_endpoint(shop_domain: str, api_version: str = API_VERSION) -> str:
    return f"https://{shop_domain}/admin/api/{api_version}/graphql.json"


def normalize_domain(shop_domain: str) -> str:
    if not shop_domain:
        return ""
    cleaned = shop_domain.replace("https://", "").replace("http://", "").strip("/")
    return cleaned


def extract_variant_id(variant_gid: str) -> str:
    return variant_gid.split("/")[-1] if variant_gid else ""


def money_to_str(value: Any) -> str:
    if isinstance(value, dict):
        amount = value.get("amount")
        return f"RM {amount}" if amount is not None else str(value)
    return f"RM {value}" if isinstance(value, (int, float, str)) else str(value)


class ShopifyStockAll:
    def __init__(
        self,
        *,
        shop_domain: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: str = API_VERSION,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.shop_domain = normalize_domain(shop_domain or SHOP_DOMAIN)
        self.access_token = access_token or SHOPIFY_ACCESS_TOKEN
        self.api_version = api_version
        self.session = session or requests
        if not self.shop_domain or not self.access_token:
            raise RuntimeError("Missing shop domain or access token. Set SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN.")

    def _post(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = graphql_endpoint(self.shop_domain, self.api_version)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shopify-Access-Token": self.access_token,
        }
        resp = self.session.post(endpoint, json={"query": query, "variables": variables}, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"GraphQL errors: {body['errors']}")
        return body.get("data", {})

    def fetch_all_products(self) -> List[Dict[str, Any]]:
        """
        Retrieve all products with pagination; returns list of product nodes.
        """
        products: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            variables = {
                "cursor": cursor,
                "pageSize": PRODUCTS_PAGE_SIZE,
                "variantSize": VARIANTS_PAGE_SIZE,
            }
            data = self._post(PRODUCT_QUERY, variables)
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

    def build_product_link(self, product: Dict[str, Any]) -> str:
        if product.get("onlineStoreUrl"):
            return product["onlineStoreUrl"]
        handle = product.get("handle") or ""
        return f"https://{self.shop_domain}/products/{handle}"

    def build_payload(self) -> Dict[str, Any]:
        variants_all: List[Dict[str, Any]] = []
        for product in self.fetch_all_products():
            if product.get("status") != "ACTIVE":
                continue
            if (product.get("totalInventory") or 0) <= 0:
                continue
            product_link = self.build_product_link(product)
            variants_payload = product.get("variants", {}).get("edges") or []
            for v_edge in variants_payload:
                variant = v_edge.get("node") or {}
                available_for_sale = bool(variant.get("availableForSale"))
                inventory_quantity = variant.get("inventoryQuantity") or 0
                # Require both availableForSale and positive inventory.
                if not (available_for_sale and inventory_quantity > 0):
                    continue
                variant_gid = variant.get("id") or ""
                variant_id = extract_variant_id(variant_gid)
                variant_link = f"{product_link}?variant={variant_id}" if variant_id else ""
                variants_all.append(
                    {
                        "Product": product.get("title") or "",
                        "Variant": variant.get("title") or "",
                        "Price": money_to_str(variant.get("price")),
                        "Availability": inventory_quantity,
                        "Variant Link": variant_link,
                        "Product Link": product_link,
                    }
                )
        return {"variants": variants_all}

    def print_cli(self) -> None:
        import json

        payload = self.build_payload()
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    bot = ShopifyStockAll()
    bot.print_cli()


if __name__ == "__main__":
    main()
