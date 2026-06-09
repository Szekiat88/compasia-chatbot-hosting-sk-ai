#!/bin/bash
# Incremental product sync — runs twice daily via cron.
# Starts the SSM tunnels, waits for them to be ready, syncs, then tears down.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python"
LOG="$SCRIPT_DIR/sync.log"
AWS_PROFILE="${AWS_PROFILE:-marketplace}"

# ── Logging ──────────────────────────────────────────────────────────────────
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "===== sync started ====="

# ── Load .env so DB credentials are available ────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# ── Start SSM tunnel for marketplace DB (port 5421) ──────────────────────────
log "Starting SSM tunnel → marketplace DB (port 5421)..."
aws ssm start-session \
    --region ap-southeast-5 \
    --target i-046d2ea75fdd7997d \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters '{"portNumber":["5432"],"localPortNumber":["5421"],"host":["my-compasia-uat-marketplace.c5saoe4641k5.ap-southeast-5.rds.amazonaws.com"]}' \
    --profile "$AWS_PROFILE" &
TUNNEL_PID=$!

# Wait until port 5421 is accepting TCP connections (max 30 s)
log "Waiting for tunnel on port 5421 to become ready..."
READY=0
for i in $(seq 1 30); do
    if nc -z 127.0.0.1 5421 2>/dev/null; then
        log "  Port 5421 is ready (after ${i}s)."
        READY=1
        break
    fi
    sleep 1
done
if [[ $READY -eq 0 ]]; then
    log "ERROR: Port 5421 did not become ready within 30 seconds. Aborting."
    kill "$TUNNEL_PID" 2>/dev/null || true
    exit 1
fi

# ── Run the sync ──────────────────────────────────────────────────────────────
log "Running sync_new_products.py --rebuild-index ..."
cd "$SCRIPT_DIR"
"$PYTHON" sync_new_products.py --rebuild-index >> "$LOG" 2>&1
EXIT_CODE=$?

# ── Tear down tunnel ──────────────────────────────────────────────────────────
kill "$TUNNEL_PID" 2>/dev/null || true
log "SSM tunnel closed."

if [[ $EXIT_CODE -eq 0 ]]; then
    log "Sync completed successfully."
else
    log "ERROR: sync exited with code $EXIT_CODE."
fi

log "===== sync finished ====="
exit $EXIT_CODE
