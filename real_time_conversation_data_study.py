import pathlib
from html import unescape
from html.parser import HTMLParser

import pandas as pd
import requests


def refresh_access_token() -> str:
    token_url = "https://accounts.zoho.com/oauth/v2/token"
    params = {
        "grant_type": "refresh_token",
        "client_id": "1000.1OISMVF0QNM472CQ9B5L21HMOWHRNO",
        "client_secret": "4c36f08a8d6f5c9d48fed393e7943b6ef34c271b51",
        "refresh_token": "1000.67867edf9ae0c48b1d851595591317bf.f93896c63df17b9e368ab2eb7d1b6864",
    }
    response = requests.post(token_url, params=params)
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_threads(ticket_id: str, headers: dict) -> dict:
    url = f"https://desk.zoho.com/api/v1/tickets/{ticket_id}/threads"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def fetch_thread_detail(ticket_id: str, thread_id: str, headers: dict) -> dict:
    url = f"https://desk.zoho.com/api/v1/tickets/{ticket_id}/threads/{thread_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


class PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
        if tag in {"br", "p", "div", "table", "tr"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:  # noqa: ARG002
        if tag in {"p", "div", "table", "tr"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def get_text(self) -> str:
        cleaned_chunks: list[str] = []
        for chunk in self._chunks:
            if chunk == "\n":
                cleaned_chunks.append("\n")
                continue

            text = chunk.strip()
            if text:
                cleaned_chunks.append(text)

        combined = " ".join(cleaned_chunks)
        combined = combined.replace("\n ", "\n").replace(" \n", "\n")

        normalized_lines = [" ".join(line.split()) for line in combined.splitlines()]
        collapsed = "\n".join(line for line in normalized_lines if line)
        return unescape(collapsed.strip())


def html_to_text(html_content: str | None) -> str:
    if not html_content:
        return ""

    parser = PlainTextExtractor()
    parser.feed(html_content)
    return parser.get_text()

def main() -> None:
    
    access_token = refresh_access_token()
    headers = {"orgId": "849512981", "Authorization": f"Zoho-oauthtoken {access_token}"}

    ticket_id = '967182000177623000'
    threads_response = fetch_threads(ticket_id, headers)
    print("Hello ", threads_response)
    
    threads_data = threads_response.get("data", [])
    
    # last_thread_id = '967182000177673186'
    # detail_response = fetch_thread_detail(ticket_id, last_thread_id, headers)
    # content = html_to_text(detail_response.get("content"))

    # print("Content: ", content)


if __name__ == "__main__":
    main()
