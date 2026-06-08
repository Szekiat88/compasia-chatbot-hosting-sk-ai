# Product Sync & Vector DB Guide

End-to-end reference for the incremental product sync system — from the marketplace DB to the FAISS vector index.

---

## Architecture

```
marketplace_mercur_uat (RDS)
        │
        │  vw_changed_variants  (view — detects changes in last 12h)
        │
        ▼
sync_new_products.py
        │
        ├── upsert changed rows ──▶  marketplace_variant  (ai-grading-uat)
        │
        └── rebuild index ────────▶  build_vectors.py  ──▶  FAISS index
                                                              (.cache_semantic_search/)
```

The sync runs **twice daily (06:00 and 18:00 UTC)** via systemd timer on the Ubuntu server.

---

## Files

| File | Purpose |
|---|---|
| `sync_new_products.py` | Main sync script |
| `build_vectors.py` | Rebuilds FAISS index from `marketplace_variant` |
| `deploy/create_changed_view.sql` | Creates `vw_changed_variants` on source DB (run once) |
| `deploy/run_sync.sh` | Shell wrapper called by systemd — no SSM tunnel |
| `deploy/compasia-sync.service` | systemd service unit |
| `deploy/compasia-sync.timer` | systemd timer unit (6am + 6pm) |
| `deploy/setup.sh` | One-shot server setup script |
| `deploy/server.env` | Template `.env` for the server (DB credentials) |
| `deploy_to_server.sh` | rsync command to push code from Mac to server |

---

## The Change-Detection View

`vw_changed_variants` lives on `marketplace_mercur_uat`. It returns one row per `(src_product_id, src_variant_id)` where **any** of the following tables had a `created_at`, `updated_at`, or `deleted_at` change in the last 12 hours:

| Table | What it detects |
|---|---|
| `product` | title, status, handle changes |
| `product_variant` | manage_inventory, allow_backorder changes |
| `product_type` | product_type label changes |
| `product_option_value` | color, spec, condition, brand changes |
| `price` | price amount changes |
| `inventory_level` | stock / reserved quantity changes |

### Create the view (run once on the source DB)

```bash
psql -h my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com \
     -U szekiat -d marketplace_mercur_uat \
     -f deploy/create_changed_view.sql
```

The view uses `NOW() - INTERVAL '12 hours'` so it is always dynamic — no need to recreate it on every sync run.

---

## sync_new_products.py

### How it works

1. Connects to `marketplace_mercur_uat` (source DB)
2. JOINs against `vw_changed_variants` to extract only changed variants
3. Also queries the view for variants that are now deleted or unpublished → removes them from `marketplace_variant`
4. Upserts changed rows into `marketplace_variant` on `ai-grading-uat` (FAISS DB)
5. If `--rebuild-index` is passed, calls `build_vectors.py` to rebuild the FAISS index

### Flags

| Flag | Effect |
|---|---|
| *(none)* | Incremental — only rows changed in last 12h via view |
| `--full` | Bypass view, sync all products regardless of change time |
| `--rebuild-index` | Rebuild FAISS index after syncing |
| `--dry-run` | Preview rows without writing anything |

### Run manually

```bash
# Incremental (default)
python sync_new_products.py --rebuild-index

# Full re-sync
python sync_new_products.py --full --rebuild-index

# Preview only
python sync_new_products.py --dry-run
```

### Availability rules

- `deleted_at IS NULL` applied everywhere
- `product.status = 'published'` only
- `manage_inventory = TRUE` AND `qty > 0` → available
- `allow_backorder = TRUE` AND `qty <= 0` → available (backorder)
- All other cases → not available

---

## Server Setup (Ubuntu EC2 — 43.217.101.210)

### DB connections on the server (no SSM tunnel needed)

| DB | Host | Port |
|---|---|---|
| marketplace_mercur_uat (source) | `my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com` | 5432 |
| ai-grading-uat (FAISS DB) | `localhost` | 5432 |

### Environment variables (`~/.../compasia-chatbot-hosting-sk-ai/.env`)

```env
NEW_DB_HOST=my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com
NEW_DB_PORT=5432
NEW_DB_NAME=marketplace_mercur_uat
NEW_DB_USER=szekiat
NEW_DB_PASSWORD=<password>

DB_HOST=localhost
DB_PORT=5432
DB_NAME=ai-grading-uat
DB_USER=szekiat
DB_PASSWORD=<password>
```

### First-time setup

```bash
# SSH into server
ssh -i ~/Downloads/ec2-ai-grading-uat.pem ubuntu@43.217.101.210

# Run one-shot setup (installs Python, venv, systemd units)
cd /home/ubuntu/compasia-chatbot-hosting-sk-ai
bash deploy/setup.sh
```

### Deploy code updates (from Mac)

```bash
./deploy_to_server.sh
```

This rsyncs all code files excluding `.git`, `.env`, `venv`, `__pycache__`, `.DS_Store`.

---

## Systemd Timer

The timer fires at **06:00 UTC** and **18:00 UTC** daily. `Persistent=true` means if the server was down at fire time, it runs once on next boot.

```bash
# Check next scheduled runs
systemctl list-timers compasia-sync.timer

# Trigger manually
sudo systemctl start compasia-sync.service

# Watch live logs
journalctl -u compasia-sync -f

# Check last run status
systemctl status compasia-sync.service
```

Log file also written to: `/home/ubuntu/compasia-chatbot-hosting-sk-ai/sync.log`

---

## Full Data Flow (each scheduled run)

```
1. run_sync.sh triggers
2. sync_new_products.py starts
3. Connects to marketplace_mercur_uat
4. Queries vw_changed_variants → list of changed (product_id, variant_id)
5. Extracts full variant data for changed rows (price, spec, color, availability …)
6. Queries view again for deleted/unpublished → list to remove
7. If nothing changed → exit early (no DB write, no index rebuild)
8. Connects to ai-grading-uat
9. Upserts changed rows into marketplace_variant
10. Deletes removed rows from marketplace_variant
11. Calls build_vectors.py → reads marketplace_variant WHERE is_available = TRUE
12. Rebuilds FAISS index in .cache_semantic_search/
13. Done
```
