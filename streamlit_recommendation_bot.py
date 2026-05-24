"""Streamlit UI for the recommendation bot."""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import streamlit as st
from sentence_transformers import SentenceTransformer

from semantic_search import (
    DB_ENV_PATH,
    EMBED_MODEL,
    build_search_query,
    fetch_full_records,
    get_db_conn,
    load_cache,
    load_env_file,
    search_index,
)
from recommendation_bot import (
    _build_product_link,
    _build_variant_link,
    _build_reasons_from_genai,
    _build_human_reply,
    _filter_rows_to_models_mentioned_in_reply,
    _filter_rows_to_recommended_model,
    _format_cards,
    _is_refinement_query,
    _recommended_in_results,
)

TOP_K = 3
CANDIDATE_K = 50


def has_streamlit_runtime() -> bool:
    runtime = getattr(st, "runtime", None)
    exists = getattr(runtime, "exists", None)
    if callable(exists):
        try:
            return bool(exists())
        except Exception:
            return False
    return False


@st.cache_resource(show_spinner=False)
def load_model() -> SentenceTransformer:
    return SentenceTransformer(EMBED_MODEL, device="cpu")


@st.cache_resource(show_spinner=False)
def load_index_and_map() -> Tuple[object, List[Tuple[int, int]]]:
    cache = load_cache()
    meta = cache.get("meta", {})
    if not meta:
        raise RuntimeError("Cache missing. Run build_vectors.py first.")
    if meta.get("model") != EMBED_MODEL:
        raise RuntimeError("Cache model mismatch. Rebuild vectors with build_vectors.py.")
    return cache["index"], cache["id_map"]


def ensure_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "memory" not in st.session_state:
        st.session_state.memory = []
    if "last_effective_query" not in st.session_state:
        st.session_state.last_effective_query = None
    if "last_handles" not in st.session_state:
        st.session_state.last_handles = []


def append_message(role: str, content: str) -> None:
    content = content.strip()
    if not content:
        return
    messages: List[Dict[str, str]] = st.session_state.messages
    if messages and messages[-1]["role"] == role and messages[-1]["content"] == content:
        return
    messages.append({"role": role, "content": content})


def render_history() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


def run_search(query: str) -> Optional[str]:
    if not os.path.exists(DB_ENV_PATH):
        st.error(f"Missing {DB_ENV_PATH}.")
        return None

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

    env = load_env_file(DB_ENV_PATH)

    model = load_model()
    index, id_map = load_index_and_map()

    search_query, recommended_model, price_min, price_max = build_search_query(query, None)

    effective_query = f"{recommended_model} {search_query}".strip() if recommended_model else search_query
    if _is_refinement_query(query) and st.session_state.last_effective_query and not recommended_model:
        effective_query = f"{st.session_state.last_effective_query} {search_query}".strip()
    if not effective_query and st.session_state.last_effective_query:
        effective_query = st.session_state.last_effective_query
    if effective_query:
        st.session_state.last_effective_query = effective_query

    scores, idx, _ = search_index(model, index, effective_query, CANDIDATE_K)

    hits: List[Tuple[int, Tuple[int, int], float]] = []
    for rank, i in enumerate(idx):
        if i < 0 or i >= len(id_map):
            continue
        hits.append((rank + 1, id_map[i], float(scores[rank])))
        print("Hello_hits", hits)

    if not hits:
        return "No matches."

    with get_db_conn(env) as conn:
        keys = [h[1] for h in hits]
        records = fetch_full_records(conn, keys)
        record_map = {(int(r["product_id"]), int(r["variant_id"])): r for r in records}

    rows: List[Dict[str, object]] = []
    preferred_handles = set(st.session_state.last_handles) if _is_refinement_query(query) else set()

    def _append_row(rank: int, key: Tuple[int, int], rec: Dict[str, object]) -> None:
        rows.append(
            {
                "rank": rank,
                "handle": rec.get("handle", ""),
                "color": rec.get("color", ""),
                "spec": rec.get("spec", ""),
                "condition": rec.get("condition", ""),
                "tenure": rec.get("tenure", ""),
                "price": rec.get("price", ""),
                "reason": "",
                "variant_link": _build_variant_link(rec.get("handle", ""), key[1], rec.get("src_variant_id")),
                "product_link": _build_product_link(rec.get("handle", "")),
            }
        )

    # First pass: prefer previous handles.
    for rank, key, score in hits:
        rec = record_map.get(key, {})
        rec_price = rec.get("price")
        if price_min is not None and rec_price is not None:
            try:
                if float(rec_price) < price_min:
                    continue
            except (TypeError, ValueError):
                pass
        if price_max is not None and rec_price is not None:
            try:
                if float(rec_price) >= price_max:
                    continue
            except (TypeError, ValueError):
                pass
        handle = rec.get("handle", "")
        if preferred_handles and handle not in preferred_handles:
            continue
        _append_row(rank, key, rec)
        if len(rows) >= TOP_K:
            break

    # Second pass: fill remaining slots.
    if len(rows) < TOP_K:
        for rank, key, score in hits:
            rec = record_map.get(key, {})
            rec_price = rec.get("price")
            if price_min is not None and rec_price is not None:
                try:
                    if float(rec_price) < price_min:
                        continue
                except (TypeError, ValueError):
                    pass
            if price_max is not None and rec_price is not None:
                try:
                    if float(rec_price) >= price_max:
                        continue
                except (TypeError, ValueError):
                    pass
            handle = rec.get("handle", "")
            if preferred_handles and handle in preferred_handles:
                continue
            _append_row(rank, key, rec)
            if len(rows) >= TOP_K:
                break

    if not rows:
        if price_min is not None and price_max is not None:
            return f"No matches in range {price_min} to {price_max}."
        if price_min is not None:
            return f"No matches above {price_min}."
        if price_max is not None:
            return f"No matches under {price_max}."
        return "No matches after filtering."
    handles = [r["handle"] for r in rows if r.get("handle")]
    in_results = _recommended_in_results(recommended_model or "", handles)
    if in_results and recommended_model:
        rows = _filter_rows_to_recommended_model(rows, recommended_model)
        handles = [r["handle"] for r in rows if r.get("handle")]
        in_results = _recommended_in_results(recommended_model or "", handles)
    reasons = _build_reasons_from_genai(query, rows)
    if reasons:
        for r in rows:
            handle = r.get("handle", "")
            if handle in reasons:
                r["reason"] = reasons[handle]

    bot_reply = _build_human_reply(
        query=query,
        recommended_model=recommended_model,
        recommended_in_results=in_results,
        top_rows=rows,
        memory=st.session_state.memory,
        price_min=price_min,
        price_max=price_max,
    )
    rows = _filter_rows_to_models_mentioned_in_reply(rows, bot_reply)

    st.session_state.memory.append({"user": query, "bot": bot_reply})
    st.session_state.last_handles = [r["handle"] for r in rows if r.get("handle")]

    cards = _format_cards(rows)
    return f"{bot_reply}\n\n{cards}"


def main() -> None:
    if not has_streamlit_runtime():
        print("This app must be launched with 'streamlit run streamlit_recommendation_bot.py'.")
        return

    st.set_page_config(page_title="Recommendation Bot", layout="centered")
    st.title("Recommendation Bot")
    st.caption("Ask for phones, then refine your request. Example: 'iphone 16 pro max' then '512gb'.")

    ensure_state()
    render_history()

    prompt = st.chat_input("Ask for a phone or refine your search...")
    if prompt:
        append_message("user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Searching..."):
                response = run_search(prompt)
            if response:
                st.markdown(response)
                append_message("assistant", response)


if __name__ == "__main__":
    main()
