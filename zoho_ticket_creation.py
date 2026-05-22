"""Zoho Desk ticket creation helpers."""

from __future__ import annotations

import os
import textwrap

import requests
import json
ZOHO_ACCOUNTS_BASE_DEFAULT = "https://accounts.zoho.com"
ZOHO_DESK_ORG_ID_DEFAULT = "849512981"
ZOHO_DESK_DEPARTMENT_ID_DEFAULT = "967182000186597233"
ZOHO_OAUTH_CLIENT_ID_DEFAULT = "1000.2IIR524TK0M7SKIT4CAKDKPTHD3Z2C"
ZOHO_OAUTH_CLIENT_SECRET_DEFAULT = "499df260a7ffe1618482bdc75b36b1b684b4740aba"
ZOHO_OAUTH_GRANT_CODE_DEFAULT = ""
ZOHO_OAUTH_ACCESS_TOKEN_DEFAULT = ""
ZOHO_OAUTH_REFRESH_TOKEN_DEFAULT = ""

DEPT_GENERAL = 967182000186597233
PRIORITY_LOW = "Low"
PRIORITY_MEDIUM = "Medium"
PRIORITY_HIGH = "High"


def refresh_access_token() -> str:
    """Refresh the Zoho OAuth access token using refresh token or grant code."""
    accounts_base = os.getenv("ZOHO_OAUTH_ACCOUNTS_BASE", ZOHO_ACCOUNTS_BASE_DEFAULT).rstrip("/")
    refresh_token = os.getenv("ZOHO_OAUTH_REFRESH_TOKEN", ZOHO_OAUTH_REFRESH_TOKEN_DEFAULT)
    grant_code = os.getenv("ZOHO_OAUTH_GRANT_CODE", ZOHO_OAUTH_GRANT_CODE_DEFAULT)
    client_id = os.getenv("ZOHO_OAUTH_CLIENT_ID", ZOHO_OAUTH_CLIENT_ID_DEFAULT)
    client_secret = os.getenv("ZOHO_OAUTH_CLIENT_SECRET", ZOHO_OAUTH_CLIENT_SECRET_DEFAULT)

    if not all([client_id, client_secret]):
        raise RuntimeError("Missing Zoho OAuth credentials: client id/secret.")
    if not refresh_token and not grant_code:
        raise RuntimeError("Missing Zoho OAuth inputs: provide refresh token or grant code.")

    token_url = f"{accounts_base}/oauth/v2/token"

    def request_refresh() -> requests.Response:
        payload = {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        }
        return requests.post(token_url, data=payload, timeout=10)

    def request_grant_code() -> requests.Response:
        payload = {
            "code": grant_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
        }
        return requests.post(token_url, data=payload, timeout=10)

    if refresh_token:
        response = request_refresh()
        data = response.json() if response.text else {}
        token = data.get("access_token")
        if response.status_code < 400 and token:
            os.environ["ZOHO_OAUTH_ACCESS_TOKEN"] = token
            return token

        # Some Zoho tenants return {"error":"invalid_code"} for an invalid/expired refresh token.
        # If a grant code is configured, try once as fallback.
        if grant_code:
            fallback = request_grant_code()
            fallback_data = fallback.json() if fallback.text else {}
            fallback_token = fallback_data.get("access_token")
            if fallback.status_code < 400 and fallback_token:
                new_refresh = fallback_data.get("refresh_token")
                if new_refresh:
                    os.environ["ZOHO_OAUTH_REFRESH_TOKEN"] = new_refresh
                os.environ["ZOHO_OAUTH_ACCESS_TOKEN"] = fallback_token
                return fallback_token
            raise RuntimeError(
                "Zoho token refresh failed and grant-code fallback failed. "
                f"Refresh response: {response.text} | Grant response: {fallback.text}"
            )
        raise RuntimeError(
            "Zoho token refresh failed. "
            f"Status: {response.status_code}, response: {response.text}"
        )

    response = request_grant_code()
    if response.status_code >= 400:
        raise RuntimeError(f"Failed to get access token using grant code ({response.status_code}): {response.text}")

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Grant-code response missing access_token: {response.text}")

    new_refresh = data.get("refresh_token")
    if new_refresh:
        os.environ["ZOHO_OAUTH_REFRESH_TOKEN"] = new_refresh

    os.environ["ZOHO_OAUTH_ACCESS_TOKEN"] = token
    return token


def get_access_token() -> str:
    """Return an access token; refresh if not present."""
    token = os.getenv("ZOHO_OAUTH_ACCESS_TOKEN", ZOHO_OAUTH_ACCESS_TOKEN_DEFAULT)
    if token:
        return token
    return refresh_access_token()


def create_zoho_ticket(subject: str, description: str, department_id: int, priority: str, contact_phone: str = "") -> dict:
    """Create a Zoho Desk ticket in the given department."""
    access_token = get_access_token()
    org_id = os.getenv("ZOHO_DESK_ORG_ID", ZOHO_DESK_ORG_ID_DEFAULT)
    contact_email = os.getenv("ZOHO_DESK_CONTACT_EMAIL", "sze.kiat@compasia.com")
    contact_name = os.getenv("ZOHO_DESK_CONTACT_NAME", "Customer")

    if not all([access_token, org_id, department_id]):
        raise RuntimeError("Missing Zoho Desk settings: org id, department id, or access token.")

    url = "https://desk.zoho.com/api/v1/tickets"
    contact = {
        "email": contact_email,
        "lastName": contact_name,
    }
    if contact_phone:
        phone = contact_phone.strip()
        if phone.startswith("0"):
            phone = "+60" + phone[1:]
        contact["phone"] = phone
    print(f"Creating Zoho ticket with contact: {json.dumps(contact)}")

    body = {
        "subject": subject,
        "departmentId": department_id,
        "description": textwrap.dedent(description).strip(),
        "priority": priority,
        "status": "Escalated",
        "contact": contact,
    }

    print(f"Zoho ticket request body: {json.dumps(body)}")

    def do_request(token: str) -> requests.Response:
        headers = {
            "orgId": org_id,
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        }
        return requests.post(url, headers=headers, json=body, timeout=10)

    response = do_request(access_token)
    if response.status_code == 401:
        refreshed = refresh_access_token()
        response = do_request(refreshed)

    if response.status_code >= 400:
        raise RuntimeError(f"Zoho Desk ticket error {response.status_code}: {response.text}")
    return response.json()
