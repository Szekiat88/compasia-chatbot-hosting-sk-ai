"""
run_once_whatsapp_to_zoho.py
 
✅ No Flask / No API.
✅ Just click Run (or `python run_once_whatsapp_to_zoho.py`)
✅ Uses your HARD-CODED WhatsApp payload (Jess / Hello)
✅ Creates Zoho Desk ticket the first time
✅ Next time you run it again, it will APPEND a comment to the SAME ticket
   (because we store wa_id -> ticket_id in local SQLite: wa_zoho_map.db)
 
Install:
  pip install requests
 
Run:
  python run_once_whatsapp_to_zoho.py
"""
 
from __future__ import annotations
 
import sqlite3
import textwrap
from datetime import datetime, timezone
from typing import Optional, Tuple
 
import requests
 
 
# =========================================================
# 1) HARD-CODED ZOHO CONFIG (as requested)
# =========================================================
ZOHO_ACCOUNTS_BASE = "https://accounts.zoho.com"
 
ZOHO_DESK_ORG_ID = "849512981"
ZOHO_DESK_DEPARTMENT_ID = 967182000186597233
 
ZOHO_OAUTH_CLIENT_ID = "1000.2IIR524TK0M7SKIT4CAKDKPTHD3Z2C"
ZOHO_OAUTH_CLIENT_SECRET = "499df260a7ffe1618482bdc75b36b1b684b4740aba"
ZOHO_OAUTH_ACCESS_TOKEN = "1000.153563af416d354685656e8e7e52adbd.afc060f4caf53b791a132acc7c47a41f"
ZOHO_OAUTH_REFRESH_TOKEN = "1000.6741bee922eb1dc57d855a78c22fd0f7.c412684f546a745f2426e2bc294f78d4"
 
PRIORITY_LOW = "Low"
DEFAULT_STATUS = "Open"
DEFAULT_CONTACT_EMAIL = "customer@example.com"
 
 
# =========================================================
# 2) HARD-CODED WHATSAPP PAYLOAD (from your log)
# =========================================================
PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "861838290051731",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "601128327740",
                            "phone_number_id": "951155114754975",
                        },
                        "contacts": [
                            {"profile": {"name": "Jess"}, "wa_id": "60192585268"}
                        ],
                        "messages": [
                            {
                                "from": "60192585268",
                                "id": "wamid.HBgLNjAxOTI1ODUyNjgVAgASGBQzQjU5QjFGMkE0MUU1N0FEQzQyNQA=",
                                "timestamp": "1770630012",
                                "text": {"body": "Hello"},
                                "type": "text",
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}
 
 
# =========================================================
# 3) SQLITE (wa_id -> ticket_id mapping)
# =========================================================
DB_PATH = "wa_zoho_map.db"
 
 
def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wa_ticket_map (
            wa_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            status TEXT DEFAULT 'Open',
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn
 
 
def get_ticket_id_by_wa_id(wa_id: str) -> Optional[str]:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT ticket_id FROM wa_ticket_map WHERE wa_id=?",
            (wa_id,),
        ).fetchone()
    return row[0] if row else None
 
 
def upsert_ticket_map(wa_id: str, ticket_id: str, status: str = DEFAULT_STATUS) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO wa_ticket_map (wa_id, ticket_id, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(wa_id) DO UPDATE SET
                ticket_id=excluded.ticket_id,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (wa_id, ticket_id, status, now),
        )
 
 
# =========================================================
# 4) ZOHO OAUTH + API HELPERS
# =========================================================
def _zoho_headers(access_token: str) -> dict:
    return {
        "orgId": ZOHO_DESK_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json",
    }
 
 
def refresh_access_token() -> str:
    """
    Refresh access token using refresh token.
    Updates the global ZOHO_OAUTH_ACCESS_TOKEN.
    """
    global ZOHO_OAUTH_ACCESS_TOKEN
 
    token_url = f"{ZOHO_ACCOUNTS_BASE.rstrip('/')}/oauth/v2/token"
    payload = {
        "refresh_token": ZOHO_OAUTH_REFRESH_TOKEN,
        "client_id": ZOHO_OAUTH_CLIENT_ID,
        "client_secret": ZOHO_OAUTH_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }
 
    resp = requests.post(token_url, data=payload, timeout=15)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to refresh access token ({resp.status_code}): {resp.text}")
 
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Refresh response missing access_token: {resp.text}")
 
    ZOHO_OAUTH_ACCESS_TOKEN = token
    return token
 
 
def create_zoho_ticket(subject: str, description: str, department_id: int, priority: str) -> dict:
    """
    Create a Zoho Desk ticket. Returns JSON containing 'id'.
    """
    url = "https://desk.zoho.com/api/v1/tickets"
    body = {
        "subject": subject,
        "departmentId": department_id,
        "description": textwrap.dedent(description).strip(),
        "priority": priority,
        "status": DEFAULT_STATUS,
        "contact": {
            "email": DEFAULT_CONTACT_EMAIL,
            "lastName": subject[:80],
        },
    }
 
    def do_request(token: str) -> requests.Response:
        return requests.post(url, headers=_zoho_headers(token), json=body, timeout=15)
 
    resp = do_request(ZOHO_OAUTH_ACCESS_TOKEN)
    if resp.status_code == 401:
        resp = do_request(refresh_access_token())
 
    if resp.status_code >= 400:
        raise RuntimeError(f"Zoho Desk create ticket error {resp.status_code}: {resp.text}")
 
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"Zoho Desk create ticket: missing id in response: {data}")
    return data
 
 
def add_zoho_ticket_comment(ticket_id: str, content: str, is_public: bool = True) -> dict:
    """
    Append a comment to an existing ticket (continue conversation).
    """
    url = f"https://desk.zoho.com/api/v1/tickets/{ticket_id}/comments"
    body = {
        "content": textwrap.dedent(content).strip(),
        "isPublic": bool(is_public),
    }
 
    def do_request(token: str) -> requests.Response:
        return requests.post(url, headers=_zoho_headers(token), json=body, timeout=15)
 
    resp = do_request(ZOHO_OAUTH_ACCESS_TOKEN)
    if resp.status_code == 401:
        resp = do_request(refresh_access_token())
 
    if resp.status_code >= 400:
        raise RuntimeError(f"Zoho Desk add comment error {resp.status_code}: {resp.text}")
 
    return resp.json()
 
 
# =========================================================
# 5) WHATSAPP PAYLOAD PARSER
# =========================================================
def parse_whatsapp_payload(payload: dict) -> Tuple[str, str, str, str]:
    entry = payload["entry"][0]
    change = entry["changes"][0]
    value = change["value"]
 
    contacts = value.get("contacts", [])
    messages = value.get("messages", [])
    if not messages:
        raise ValueError("No messages[] in payload.")
 
    msg = messages[0]
    if msg.get("type") != "text":
        raise ValueError(f"Unsupported message type: {msg.get('type')}")
 
    wa_id = msg.get("from") or (contacts[0].get("wa_id") if contacts else None)
    if not wa_id:
        raise ValueError("Missing wa_id/from in payload.")
 
    name = "Customer"
    if contacts and contacts[0].get("profile", {}).get("name"):
        name = contacts[0]["profile"]["name"]
 
    text_body = msg["text"]["body"]
    message_id = msg.get("id", "")
    return wa_id, name, text_body, message_id
 
 
# =========================================================
# 6) MAIN LOGIC: create ticket OR append comment
# =========================================================
def upsert_whatsapp_to_zoho(wa_id: str, wa_name: str, message_text: str, message_id: str) -> dict:
    ticket_id = get_ticket_id_by_wa_id(wa_id)
 
    header = f"WhatsApp from {wa_name} ({wa_id})\nMessageId: {message_id}"
 
    if ticket_id:
        print(f"[OK] Found existing ticket {ticket_id} for wa_id={wa_id}. Appending comment...")
        return add_zoho_ticket_comment(
            ticket_id=ticket_id,
            content=f"{header}\n\n{message_text}",
            is_public=True,
        )
 
    print(f"[OK] No ticket found for wa_id={wa_id}. Creating new ticket...")
    created = create_zoho_ticket(
        subject=f"WhatsApp - {wa_name} ({wa_id})",
        description=f"{header}\n\n{message_text}",
        department_id=ZOHO_DESK_DEPARTMENT_ID,
        priority=PRIORITY_LOW,
    )
    new_ticket_id = created["id"]
    upsert_ticket_map(wa_id, new_ticket_id, status=created.get("status", DEFAULT_STATUS))
    print(f"[OK] Created ticket_id={new_ticket_id} and saved mapping in {DB_PATH}")
    return created
 
 
def main():
    wa_id, name, text_body, msg_id = parse_whatsapp_payload(PAYLOAD)
 
    print("Incoming WhatsApp:")
    print(f"  wa_id: {wa_id}")
    print(f"  name : {name}")
    print(f"  text : {text_body}")
    print(f"  msgId: {msg_id}")
    print("")
 
    result = upsert_whatsapp_to_zoho(
        wa_id=wa_id,
        wa_name=name,
        message_text=text_body,
        message_id=msg_id,
    )
 
    # Print final result summary
    if "id" in result:
        print(f"\nDone ✅ Zoho Ticket ID: {result['id']}")
    else:
        # comment response may not include ticket id; fetch from mapping
        print(f"\nDone ✅ Zoho Ticket ID (from mapping): {get_ticket_id_by_wa_id(wa_id)}")
 
 
if __name__ == "__main__":
    main()