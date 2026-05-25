#!/usr/bin/env python3
import os
import json
import sys
from typing import Any, Dict, List, Tuple, Optional

from _params import _T

import numpy as np
import psycopg2
import psycopg2.extras
from sentence_transformers import SentenceTransformer
import pyarrow.parquet as pq
import re as _re

try:
    import faiss  # type: ignore
except ImportError:
    faiss = None


DB_ENV_PATH = "db.env"
ENV_PATH = '.env'
EMBED_MODEL = "all-MiniLM-L6-v2"
TOP_K = 5
CANDIDATE_K = 50
EMBED_BATCH = 200
CACHE_DIR = os.getenv("SEMANTIC_CACHE_DIR", ".cache_semantic_search")
CACHE_META = "meta.json"
CACHE_INDEX = "index.faiss"
CACHE_EMBEDDINGS = "embeddings.parquet"

class _NumpyIPIndex:
    """Minimal FAISS-like index fallback using inner-product search."""

    def __init__(self, vectors: np.ndarray):
        self.vectors = vectors.astype(np.float32, copy=False)

    def search(self, query_vectors: np.ndarray, top_k: int) -> Tuple[np.ndarray, np.ndarray]:
        if self.vectors.size == 0 or top_k <= 0:
            empty_scores = np.empty((query_vectors.shape[0], 0), dtype=np.float32)
            empty_idx = np.empty((query_vectors.shape[0], 0), dtype=np.int64)
            return empty_scores, empty_idx
        scores = np.matmul(query_vectors, self.vectors.T)
        k = min(top_k, self.vectors.shape[0])
        part = np.argpartition(-scores, kth=k - 1, axis=1)[:, :k]
        row_ids = np.arange(scores.shape[0])[:, None]
        part_scores = scores[row_ids, part]
        order = np.argsort(-part_scores, axis=1)
        top_idx = part[row_ids, order].astype(np.int64, copy=False)
        top_scores = part_scores[row_ids, order].astype(np.float32, copy=False)
        return top_scores, top_idx


def load_env_file(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
            elif ":" in line:
                key, val = line.split(":", 1)
            else:
                continue
            env[key.strip()] = val.strip()
    return env


def get_db_conn(env: Dict[str, str]):
    return psycopg2.connect(
        host=env.get("DB_HOST"),
        port=env.get("DB_PORT"),
        dbname=env.get("DB_NAME"),
        user=env.get("DB_USER"),
        password=env.get("DB_PASSWORD"),
    )


def embed_texts(model: SentenceTransformer, texts: List[str]) -> np.ndarray:
    arr = model.encode(
        texts,
        batch_size=EMBED_BATCH,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return arr.astype(np.float32, copy=False)


def search_index(
    model: SentenceTransformer,
    index: Any,
    query: str,
    top_k: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    q_vec = embed_texts(model, [query])
    scores, idx = index.search(q_vec, top_k)
    return scores[0], idx[0], q_vec[0]


def _marketplace_table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'marketplace_variant'
        """)
        return cur.fetchone() is not None


def fetch_full_records(conn, keys: List[Tuple[int, int]]) -> List[Dict[str, object]]:
    if not keys:
        return []
    cols = "product_id, variant_id, handle, vendor, product_type, color, spec, condition, price, tenure"
    source_sql = f"SELECT {cols}, src_variant_id FROM marketplace_variant WHERE is_available = TRUE"
    sql = f"""
        SELECT s.*
        FROM ({source_sql}) s
        JOIN (VALUES %s) v(product_id, variant_id)
          ON s.product_id = v.product_id AND s.variant_id = v.variant_id
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        psycopg2.extras.execute_values(cur, sql, keys)
        return list(cur.fetchall())


def fetch_available_models(conn, prefix: str, limit: int = 50) -> List[str]:
    sql = """
        SELECT DISTINCT handle
        FROM marketplace_variant
        WHERE handle ILIKE %s
          AND is_available = TRUE
        ORDER BY handle
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (prefix + "%", limit))
        return [r[0] for r in cur.fetchall() if r[0]]


def build_search_query(
    query: str,
    available_models: Optional[List[str]] = None,
) -> Tuple[str, str, Optional[float], Optional[float]]:
    text = query.strip()
    price_min: Optional[float] = None
    price_max: Optional[float] = None

    # "between X and/to Y" or "RM X to Y"
    m = _re.search(
        r'(?:between|from)\s*(?:rm)?\s*([\d,]+)\s*(?:to|and|-)\s*(?:rm)?\s*([\d,]+)',
        text, _re.I,
    )
    if not m:
        m = _re.search(r'(?:rm)?\s*([\d,]+)\s*(?:to|-)\s*(?:rm)?\s*([\d,]+)', text, _re.I)
    if m:
        price_min = float(m.group(1).replace(',', ''))
        price_max = float(m.group(2).replace(',', ''))
    else:
        # "under / below / less than X"
        m2 = _re.search(r'(?:under|below|less\s+than)\s*(?:rm)?\s*([\d,]+)', text, _re.I)
        if m2:
            price_max = float(m2.group(1).replace(',', ''))
        # "above / over / more than X"
        m3 = _re.search(r'(?:above|over|more\s+than)\s*(?:rm)?\s*([\d,]+)', text, _re.I)
        if m3:
            price_min = float(m3.group(1).replace(',', ''))

    # Strip price clauses to get a cleaner search query
    search_query = _re.sub(
        r'(?:under|below|above|over|less\s+than|more\s+than|between|from)\s*(?:rm)?\s*[\d,]+'
        r'(?:\s*(?:to|and|-)\s*(?:rm)?\s*[\d,]+)?',
        '', text, flags=_re.I,
    )
    search_query = _re.sub(r'(?:rm)?\s*[\d,]+\s*(?:to|-)\s*(?:rm)?\s*[\d,]+', '', search_query, flags=_re.I)
    search_query = _re.sub(r'\s+', ' ', search_query).strip() or text

    # Match against available model handles (exact substring)
    recommended_model = ""
    if available_models:
        q_lower = text.lower()
        for mdl in available_models:
            if mdl.lower() in q_lower:
                recommended_model = mdl
                break

    return search_query, recommended_model, price_min, price_max


_PRODUCT_FIELDS = ["vendor", "product_type", "handle", "color", "spec", "condition", "tenure", "price", "src_variant_id"]


def load_cache(cache_dir: Optional[str] = None) -> Dict[str, object]:
    dir_ = cache_dir or os.getenv("SEMANTIC_CACHE_DIR", CACHE_DIR)
    meta_path = os.path.join(dir_, CACHE_META)
    index_path = os.path.join(dir_, CACHE_INDEX)
    embed_path = os.path.join(dir_, CACHE_EMBEDDINGS)
    if not (os.path.exists(meta_path) and os.path.exists(embed_path)):
        return {}
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    table = pq.read_table(embed_path)
    product_ids = table["product_id"].to_numpy().astype(np.int64, copy=False)
    variant_ids = table["variant_id"].to_numpy().astype(np.int64, copy=False)
    embeddings = np.array(table["embedding"].to_pylist(), dtype=np.float32)
    if faiss is not None and os.path.exists(index_path):
        index = faiss.read_index(index_path)
    else:
        index = _NumpyIPIndex(embeddings)
    id_map = list(zip(product_ids.tolist(), variant_ids.tolist()))
    col_names = table.column_names
    record_map: Dict[tuple, Dict[str, object]] = {}
    for i, (pid, vid) in enumerate(id_map):
        rec: Dict[str, object] = {"product_id": pid, "variant_id": vid}
        for field in _PRODUCT_FIELDS:
            if field in col_names:
                val = table[field][i].as_py()
                rec[field] = val if val else None
        record_map[(pid, vid)] = rec
    return {"meta": meta, "vectors": embeddings, "index": index, "id_map": id_map, "record_map": record_map}


def main() -> int:
    if not os.path.exists(DB_ENV_PATH):
        print(f"Missing {DB_ENV_PATH}.")
        return 1

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

    env = load_env_file(DB_ENV_PATH)

    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = input("Enter search query: ").strip()
    if not query:
        print("Query is required.")
        return 1

    print(f"Loading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL, device="cpu")

    cache = load_cache()
    meta = cache.get("meta", {})
    if not meta:
        print("Cache missing. Run build_vectors.py first.")
        return 1
    if meta.get("model") != EMBED_MODEL:
        print("Cache model mismatch. Rebuild vectors with build_vectors.py.")
        return 1

    index = cache["index"]
    id_map = cache["id_map"]

    available_models = None

    search_query, recommended_model, price_min, price_max = build_search_query(query, available_models)
    if recommended_model:
        print(f"AI recommended model: {recommended_model}")
    if price_min is not None:
        print(f"AI parsed price_min: {price_min}")
    if price_max is not None:
        print(f"AI parsed price_max: {price_max}")
    effective_query = (
        f"{recommended_model} {search_query}".strip() if recommended_model else search_query
    )
    print(f"Embedding query and searching: {effective_query}")
    scores, idx, q_vec = search_index(model, index, effective_query, CANDIDATE_K)
    print(f"Query embedding shape: {q_vec.shape}")
    print(f"FAISS top scores: {[float(s) for s in scores]}")
    hits = []
    for rank, i in enumerate(idx):
        if i < 0 or i >= len(id_map):
            continue
        hits.append((rank + 1, id_map[i], float(scores[rank])))

    if not hits:
        print("No matches.")
        return 0

    with get_db_conn(env) as conn:
        keys = [h[1] for h in hits]
        records = fetch_full_records(conn, keys)
        record_map = {(int(r["product_id"]), int(r["variant_id"])): r for r in records}

    shown = 0
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
        print(f"\nRank {rank} | Score {score:.4f}")
        print(f"product_id: {key[0]}, variant_id: {key[1]}")
        for k in [
            "vendor",
            "product_type",
            "handle",
            "color",
            "spec",
            "condition",
            "tenure",
            "price",
        ]:
            if k in rec:
                print(f"{k}: {rec[k]}")
        shown += 1
        if shown >= TOP_K:
            break
    if shown == 0 and (price_min is not None or price_max is not None):
        if price_min is not None and price_max is not None:
            print(f"No matches in range {price_min} to {price_max}.")
        elif price_min is not None:
            print(f"No matches above {price_min}.")
        else:
            print(f"No matches under {price_max}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
