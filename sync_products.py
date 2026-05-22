"""
One command to sync products and deploy the updated index to Railway.

    python sync_products.py

What it does:
  1. Syncs Shopify products into the local PostgreSQL database
  2. Rebuilds the FAISS semantic search index from the updated products
  3. Copies the new index into the engine_matching repo and pushes to git
     → Railway auto-redeploys with the new index baked in
  4. Also uploads the index directly to the running Railway instance
     → Takes effect immediately without waiting for a redeploy
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
ENGINE_REPO = Path(os.getenv("ENGINE_MATCHING_REPO", BASE_DIR / ".." / "engine_matching")).resolve()
LOCAL_CACHE = BASE_DIR / ".cache_semantic_search"
REMOTE_CACHE = ENGINE_REPO / "cache"
CACHE_FILES = ["meta.json", "index.faiss", "embeddings.parquet"]


def run(label: str, cmd: list, cwd=None, extra_env: dict | None = None):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    env = {**os.environ, **(extra_env or {})}
    result = subprocess.run(cmd, cwd=cwd or BASE_DIR, env=env)
    if result.returncode != 0:
        print(f"\n✗ Failed: {label}")
        sys.exit(result.returncode)
    print(f"✓ Done: {label}")


def step1_sync_shopify():
    run("Step 1/4 — Sync Shopify products to database", [sys.executable, "shopify_stock_sync.py"])


def step2_build_index():
    run(
        "Step 2/4 — Build FAISS index from products",
        [sys.executable, "build_vectors.py"],
        extra_env={"DB_ENV_PATH": str(BASE_DIR / "db.env")},
    )


def step3_push_to_git():
    print(f"\n{'='*50}")
    print(f"  Step 3/4 — Copy index to Railway repo and push")
    print(f"{'='*50}")

    if not ENGINE_REPO.exists():
        print(f"  ⚠ Engine repo not found at {ENGINE_REPO} — skipping git push.")
        print("    Set ENGINE_MATCHING_REPO in .env to fix this.")
        return

    REMOTE_CACHE.mkdir(parents=True, exist_ok=True)
    missing = [f for f in CACHE_FILES if not (LOCAL_CACHE / f).exists()]
    if missing:
        print(f"  ✗ Cache files missing: {missing} — did build_vectors.py succeed?")
        sys.exit(1)

    for fname in CACHE_FILES:
        shutil.copy(LOCAL_CACHE / fname, REMOTE_CACHE / fname)
        print(f"  Copied {fname}")

    subprocess.run(["git", "add", "cache/"], cwd=ENGINE_REPO)
    result = subprocess.run(
        ["git", "commit", "-m", "Update FAISS product index"],
        cwd=ENGINE_REPO, capture_output=True, text=True,
    )
    if "nothing to commit" in result.stdout + result.stderr:
        print("  ✓ Index unchanged — no git commit needed.")
        return

    push = subprocess.run(["git", "push"], cwd=ENGINE_REPO)
    if push.returncode != 0:
        print("  ⚠ Git push failed — Railway will use the old index until next successful push.")
    else:
        print("  ✓ Pushed — Railway will redeploy automatically.")


def step4_upload_live():
    run("Step 4/4 — Upload index to running Railway instance", [sys.executable, "upload_vectors.py"])


if __name__ == "__main__":
    step1_sync_shopify()
    step2_build_index()
    step3_push_to_git()
    step4_upload_live()
    print("\n✅ All done! Products are synced and Railway has the latest index.")
