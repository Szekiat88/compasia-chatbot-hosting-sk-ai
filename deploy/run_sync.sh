#!/bin/bash
# Ubuntu server version — no SSM tunnel needed (direct RDS + local DB access).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # project root
PYTHON="$SCRIPT_DIR/venv/bin/python"
LOG="$SCRIPT_DIR/sync.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "===== sync started ====="

# Load server environment variables
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

cd "$SCRIPT_DIR"
"$PYTHON" sync_new_products.py --rebuild-index >> "$LOG" 2>&1
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    log "Sync completed successfully."
else
    log "ERROR: sync exited with code $EXIT_CODE — check sync.log for details."
fi

log "===== sync finished ====="
exit $EXIT_CODE
