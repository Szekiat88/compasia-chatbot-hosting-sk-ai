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


def save_records(
    thread_rows: list[dict],
    detail_rows: list[dict],
    content_rows: list[dict],
    output_path: pathlib.Path,
) -> None:
    with pd.ExcelWriter(output_path) as writer:

        wrote_something = False

        if thread_rows:
            pd.DataFrame(thread_rows).to_excel(writer, sheet_name="threads", index=False)
            wrote_something = True

        if detail_rows:
            pd.DataFrame(detail_rows).to_excel(writer, sheet_name="details", index=False)
            wrote_something = True

        if content_rows:
            pd.DataFrame(content_rows).to_excel(writer, sheet_name="content", index=False)
            wrote_something = True

        # Fallback to avoid IndexError: no visible sheet
        if not wrote_something:
            pd.DataFrame({"message": ["No data collected"]}).to_excel(
                writer, sheet_name="empty", index=False
            )


def load_ticket_ids(ticket_source, sheet_name: str | None = None) -> list[str]:
    excel_file = pd.ExcelFile(ticket_source)
    df = pd.read_excel(excel_file, sheet_name=0)
    print(df)


    available_sheets = excel_file.sheet_names
  
    print(f"Available worksheets in {excel_file}: {available_sheets or '[] (no sheets detected)'}")

    tickets_df = pd.read_excel(excel_file, usecols=[0], sheet_name=sheet_name)

    tickets_df = tickets_df.dropna()
    return tickets_df.iloc[:, 0].astype(str).tolist()


def main() -> None:
    input_sheet_name = "Email_1_message"  # Specify the sheet name when the source is an Excel file
    output_path = pathlib.Path("data/zoho_thread_records.xlsx")

    ticket_ids = load_ticket_ids("hello_fixed.xlsx", input_sheet_name)#, sheet_name=input_sheet_name)

    access_token = refresh_access_token()
    headers = {"orgId": "849512981", "Authorization": f"Zoho-oauthtoken {access_token}"}

    thread_rows: list[dict] = []
    detail_rows: list[dict] = []
    content_rows: list[dict] = []

    for ticket_id in ticket_ids:
        try:
            threads_response = fetch_threads(ticket_id, headers)
        except requests.HTTPError as exc:
            print(f"Skipping ticket {ticket_id}: {exc}")
            continue
        except requests.RequestException as exc:  # pragma: no cover - network dependent
            print(f"Skipping ticket {ticket_id} due to network error: {exc}")
            continue

        threads_data = threads_response.get("data", [])

        for thread in threads_data:
            thread_id = thread.get("id")
            author = thread.get("author").get("name")
            if thread_id is None:
                print(f"Skipping thread without id for ticket {ticket_id}: {thread}")
                continue

            thread_id_str = str(thread_id)
            thread_rows.append({"ticket_id": ticket_id, **thread})

            try:
                detail_response = fetch_thread_detail(ticket_id, thread_id_str, headers)
            except requests.RequestException as exc:  # pragma: no cover - network dependent
                print(f"Skipping detail fetch for ticket {ticket_id}, thread {thread_id_str}: {exc}")
            else:
                detail_data = detail_response.get("data")
                print(detail_data)

                detail_rows.append({"ticket_id": ticket_id, "thread_id": thread_id_str, **detail_response})
                content_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "thread_id": thread_id_str,
                        "author_name": author,
                        "content": detail_response.get("content"),
                        "content_plain": html_to_text(detail_response.get("content")),
                    }
                )

        save_records(thread_rows, detail_rows, content_rows, output_path)

    if thread_rows:
        print({"data": thread_rows})


if __name__ == "__main__":
    main()
