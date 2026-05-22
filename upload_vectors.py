"""
Upload the local FAISS index to the Railway engine matching service.

Run this after shopify_stock_sync.py + build_vectors.py whenever products change:
    python upload_vectors.py

Required env vars:
    ENGINE_MATCHING_API_URL  - Railway service URL (or set in .env)
    UPLOAD_SECRET            - must match UPLOAD_SECRET set on Railway
"""
import base64
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = Path(".cache_semantic_search")
RAILWAY_URL = os.getenv("ENGINE_MATCHING_API_URL", "https://chatbotenginematching-production.up.railway.app").rstrip("/")
UPLOAD_SECRET = os.getenv("UPLOAD_SECRET", "")


def read_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def main() -> int:
    meta_path = CACHE_DIR / "meta.json"
    index_path = CACHE_DIR / "index.faiss"
    embeddings_path = CACHE_DIR / "embeddings.parquet"

    for p in [meta_path, index_path, embeddings_path]:
        if not p.exists():
            print(f"Missing cache file: {p}")
            print("Run build_vectors.py first to generate the FAISS index.")
            return 1

    print(f"Uploading FAISS index to {RAILWAY_URL}/upload-cache ...")
    payload = {
        "meta": read_b64(meta_path),
        "index": read_b64(index_path),
        "embeddings": read_b64(embeddings_path),
    }

    headers = {"Content-Type": "application/json"}
    if UPLOAD_SECRET:
        headers["X-Upload-Secret"] = UPLOAD_SECRET

    resp = requests.post(f"{RAILWAY_URL}/upload-cache", json=payload, headers=headers, timeout=120)

    if resp.status_code == 401:
        print("Unauthorized — set UPLOAD_SECRET in .env to match Railway's UPLOAD_SECRET.")
        return 1
    if not resp.ok:
        print(f"Upload failed ({resp.status_code}): {resp.text}")
        return 1

    print(f"Done. {resp.json()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
