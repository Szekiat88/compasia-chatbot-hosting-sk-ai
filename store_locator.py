"""Store locator utilities — load stores_location.csv and match by user location."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from _params import _T

BASE_DIR = Path(__file__).resolve().parent
STORES_CSV = Path(__file__).resolve().parent / "stores_location.csv"

# Maps normalized search terms to substrings that appear in CSV location/name fields.
# Longer entries are matched first (see detect_location).
LOCATION_MAP: dict[str, list[str]] = {
    # Wilayah Persekutuan — KL
    "kuala lumpur": ["Kuala Lumpur"],
    "kl": ["Kuala Lumpur"],
    "kl sentral": ["KL Sentral", "Stesen Sentral"],
    "bukit jalil": ["Bukit Jalil"],
    "bukit bintang": ["Bukit Bintang"],
    "chow kit": ["Chow Kit"],
    "wangsa maju": ["Wangsa Maju", "Setapak"],
    "setapak": ["Setapak"],
    "kl timur": ["KL Timur", "KL East"],
    "kl east": ["KL East", "KL Timur"],
    # Wilayah Persekutuan — Putrajaya
    "putrajaya": ["Putrajaya"],
    "ioi city": ["iOi", "IOI City"],
    # Selangor
    "selangor": ["Selangor"],
    "petaling jaya": ["Petaling Jaya"],
    "pj": ["Petaling Jaya"],
    "subang jaya": ["Subang Jaya", "Subang"],
    "sj": ["Subang Jaya", "Subang"],
    "subang": ["Subang"],
    "puchong": ["Puchong"],
    "klang": ["Klang"],
    "shah alam": ["Shah Alam"],
    "ampang": ["Ampang"],
    "seri kembangan": ["Seri Kembangan"],
    "rawang": ["Rawang"],
    "semenyih": ["Semenyih"],
    "bandar kinrara": ["Bandar Kinrara"],
    "putra heights": ["Putra Heights"],
    "kuala selangor": ["Kuala Selangor"],
    "puncak alam": ["Puncak Alam"],
    "ulu kelang": ["Ulu Klang", "Hulu Kelang"],
    "hulu kelang": ["Ulu Klang", "Hulu Kelang"],
    # Pulau Pinang
    "penang": ["Pulau Pinang", "Penang"],
    "pg": ["Pulau Pinang", "Penang"],
    "pulau pinang": ["Pulau Pinang"],
    "bukit mertajam": ["Bukit Mertajam"],
    "perai": ["Perai"],
    "seberang jaya": ["Seberang Jaya"],
    "bertam": ["Bertam"],
    "kepala batas": ["Kepala Batas"],
    # Johor
    "johor bahru": ["Johor Bahru"],
    "johor": ["Johor"],
    "jb": ["Johor Bahru"],
    "ulu tiram": ["Ulu Tiram"],
    "masai": ["Masai"],
    "pontian": ["Pontian"],
    "kluang": ["Kluang"],
    "kulai": ["Kulai"],
    "batu pahat": ["Batu Pahat"],
    # Perak
    "perak": ["Perak"],
    "ipoh": ["Ipoh"],
    "kampar": ["Kampar"],
    "teluk intan": ["Teluk Intan"],
    "seri iskandar": ["Seri Iskandar"],
    "parit buntar": ["Parit Buntar"],
    "meru": ["Meru"],
    # Kedah
    "kedah": ["Kedah"],
    "alor setar": ["Alor Setar"],
    "sungai petani": ["Sungai Petani"],
    "sg petani": ["Sungai Petani"],
    # Kelantan
    "kelantan": ["Kelantan"],
    "kota bharu": ["Kota Bharu"],
    "kb": ["Kota Bharu"],
    "pasir puteh": ["Pasir Puteh"],
    "tunjong": ["Tunjong"],
    # Terengganu
    "terengganu": ["Terengganu"],
    "kuala terengganu": ["Kuala Terengganu"],
    "kt": ["Kuala Terengganu"],
    "jerteh": ["Jerteh", "Jertih"],
    "jertih": ["Jerteh", "Jertih"],
    "besut": ["Besut"],
    "gong badak": ["Gong Badak"],
    # Pahang
    "pahang": ["Pahang"],
    "kuantan": ["Kuantan"],
    "jengka": ["Jengka"],
    "indera mahkota": ["Indera Mahkota"],
    "bandar tun razak": ["Bandar Tun Razak"],
    # Melaka
    "melaka": ["Melaka"],
    "malacca": ["Melaka"],
    "jasin": ["Jasin"],
    "ayer keroh": ["Ayer Keroh"],
    "pulau sebang": ["Pulau Sebang"],
    "alor gajah": ["Alor Gajah"],
    # Negeri Sembilan
    "negeri sembilan": ["Negeri Sembilan"],
    "seremban": ["Seremban"],
    "senawang": ["Senawang"],
    "kuala pilah": ["Kuala Pilah"],
    # Sabah
    "sabah": ["Sabah"],
    "kota kinabalu": ["Kota Kinabalu"],
    "kk": ["Kota Kinabalu"],
    "sandakan": ["Sandakan"],
    # Sarawak
    "sarawak": ["Sarawak"],
    "kuching": ["Kuching"],
    "bintulu": ["Bintulu"],
    # Perlis
    "perlis": ["Perlis"],
    "kangar": ["Kangar"],
}

_MALAY_KEYWORDS = {
    "kedai", "berdekatan", "dekat", "cawangan", "mana", "boleh", "saya",
    "sini", "kawasan", "bandar", "nak", "beli", "ada", "tak", "tempat",
    "outlet", "lokasi", "cari", "pergi", "tunjuk", "alamat", "waktu",
}

# Single strong signal is enough to flag a location query
_STRONG_LOCATION_SIGNALS = {
    "nearest", "nearby", "near", "berdekatan", "walk",
    "physical", "cawangan", "branch", "outlet", "kedai", "alamat",
}

# Two weak signals together also flag it
_WEAK_LOCATION_SIGNALS = {
    "where", "store", "stores", "shop", "shops", "location", "find", "pergi", "visit", "lokasi",
}

# Store-presence words: when combined with a detected location, flag as store query
_STORE_PRESENCE_WORDS = {"store", "stores", "shop", "shops", "outlet", "outlets", "branch", "kedai"}

_stores_df: pd.DataFrame | None = None


def load_stores(csv_path: Path | None = None) -> pd.DataFrame:
    """Load stores CSV into a DataFrame, cached after first read."""
    global _stores_df
    if _stores_df is not None and csv_path is None:
        return _stores_df

    path = csv_path or STORES_CSV
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    for col in ("name", "location", "operatingHours", "whatsappLink"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()

    if csv_path is None:
        _stores_df = df
    return df


def detect_location(user_message: str) -> str | None:
    """
    Return the first location keyword found in user_message, or None.
    Longer phrases are tested before shorter ones to avoid partial matches
    (e.g. 'johor bahru' is tested before 'johor').
    """
    msg = user_message.lower()
    # Sort longest-first so multi-word terms win over their substrings
    sorted_terms = sorted(LOCATION_MAP.keys(), key=len, reverse=True)
    for term in sorted_terms:
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, msg):
            return term
    return None


def find_matching_stores(
    location_term: str,
    stores_df: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Return (active_stores, closed_stores) matching the location term.
    Each store is a plain dict with: name, location, whatsappLink, operatingHours.
    """
    search_terms = LOCATION_MAP.get(location_term.lower(), [location_term])
    pattern = "|".join(re.escape(t) for t in search_terms)

    mask = (
        stores_df["location"].str.contains(pattern, case=False, na=False, regex=True)
        | stores_df["name"].str.contains(pattern, case=False, na=False, regex=True)
    )
    matched = stores_df[mask]

    is_closed = matched["name"].str.contains("Temporarily Closed", case=False, na=False)

    def _row_to_dict(row: pd.Series) -> dict[str, Any]:
        return {
            "name": row["name"],
            "location": row["location"],
            "whatsappLink": row["whatsappLink"],
            "operatingHours": row["operatingHours"],
        }

    active = [_row_to_dict(r) for _, r in matched[~is_closed].iterrows()]
    closed = [_row_to_dict(r) for _, r in matched[is_closed].iterrows()]
    return active, closed


def _build_store_spec(
    user_message: str,
    active_stores: list[dict[str, Any]],
    language: str,
) -> str:
    """Build the spec used to generate the final reply to the user."""
    display_stores = active_stores[:5]
    stores_text = "\n".join(
        f"- {s['name']}: {s['location']} | Hours: {s['operatingHours']} | WhatsApp: {s['whatsappLink']}"
        for s in display_stores
    )

    if language == "ms":
        return _T[10].replace("{user_message}", user_message).replace("{stores_text}", stores_text)
    return _T[11].replace("{user_message}", user_message).replace("{stores_text}", stores_text)


def detect_language(text: str) -> str:
    """Return 'ms' if Malay keywords dominate the message, else 'en'."""
    tokens = set(re.findall(r"[a-zA-Z]+", text.lower()))
    return "ms" if len(tokens & _MALAY_KEYWORDS) >= 2 else "en"


def is_location_query(text: str) -> bool:
    """
    Return True when the message is asking about a physical store location.

    Triggers on:
    - Any single strong signal (nearest, outlet, branch, kedai, …)
    - Two or more weak signals together (where + store, find + location, …)
    - Short follow-up reply (≤ 6 words) that contains a known location name,
      e.g. user replies "Penang" or "I'm in Subang Jaya" after being asked
      for their area.
    - Any store-presence word (store/stores/shop/shops/outlet/kedai) combined
      with a known location name, regardless of message length, e.g.
      "do you have any stores in PJ?".
    """
    tokens = set(re.findall(r"[a-zA-Z]+", text.lower()))
    if tokens & _STRONG_LOCATION_SIGNALS:
        return True
    if len(tokens & _WEAK_LOCATION_SIGNALS) >= 2:
        return True
    if len(tokens) <= 6 and detect_location(text) is not None:
        return True
    # "stores/shops in [location]" pattern — catches plural/variant store words
    if tokens & _STORE_PRESENCE_WORDS and detect_location(text) is not None:
        return True
    return False
