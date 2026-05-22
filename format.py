"""Formatting helpers for customer-facing chatbot responses."""

from __future__ import annotations


def _text(value: object, fallback: str = "-") -> str:
    if value is None:
        return fallback
    raw = str(value).strip()
    return raw if raw else fallback


def format_shopify_order(order: dict) -> str:
    """Render Shopify order payload into a concise chat-friendly format."""

    if not order:
        return "I couldn't find an order with that ID. Please recheck and try again."

    line_items = order.get("lineItems", {}).get("edges") or []
    item_lines: list[str] = []
    if line_items:
        for idx, edge in enumerate(line_items, start=1):
            node = edge.get("node", {}) if isinstance(edge, dict) else {}
            title = _text(node.get("title"))
            qty = _text(node.get("quantity"), "0")
            item_lines.append(f"{idx}) {title} x{qty}")
    else:
        item_lines.append("1) -")

    tracking_number = "-"
    tracking_url = "-"
    fulfillments = order.get("fulfillments") or []
    for fulfillment in fulfillments:
        info = fulfillment.get("trackingInfo") or []
        for track in info:
            number = _text(track.get("number"))
            url = _text(track.get("url"))
            if number != "-":
                tracking_number = number
            if url != "-":
                tracking_url = url
            if tracking_number != "-" or tracking_url != "-":
                break
        if tracking_number != "-" or tracking_url != "-":
            break

    lines: list[str] = [
        f"Order {_text(order.get('name'))}",
        "",
        "Items",
        *item_lines,
        "",
        "- Order Status: " + _text(order.get("displayFulfillmentStatus")),
        "- Total: RM " + _text(order.get("totalPrice")),
        "- Subtotal: RM " + _text(order.get("subtotalPrice")),
        "- Tax: RM " + _text(order.get("totalTax")),
        "- Tracking Number: " + tracking_number,
        "- Click here to track your order: " + tracking_url,
    ]
    return "\n".join(lines)
