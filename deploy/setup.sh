#!/bin/bash
# Run this once on the Ubuntu server to install deps and enable the timer.
# Usage:  bash deploy/setup.sh

set -euo pipefail

DEPLOY_DIR="/home/ubuntu/compasia-chatbot-hosting-sk-ai"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== 1/5  System packages ==="
sudo apt-get update -qq

# Use Python 3.11 if available; otherwise fall back to the system python3.
if apt-cache show python3.11 &>/dev/null; then
    sudo apt-get install -y python3.11 python3.11-venv python3-pip git
    PY=python3.11
else
    # Add deadsnakes PPA for 3.11 on older Ubuntu (20.04 / 22.04)
    sudo apt-get install -y software-properties-common git
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    if apt-cache show python3.11 &>/dev/null; then
        sudo apt-get install -y python3.11 python3.11-venv python3-pip
        PY=python3.11
    else
        # Last resort: use whatever python3 is already installed
        sudo apt-get install -y python3-venv python3-pip
        PY=python3
    fi
fi
echo "  Using: $PY ($(${PY} --version))"

echo "=== 2/5  Python virtual environment ==="
if [[ ! -d "$DEPLOY_DIR/venv" ]]; then
    $PY -m venv "$DEPLOY_DIR/venv"
fi
"$DEPLOY_DIR/venv/bin/pip" install --upgrade pip --quiet
"$DEPLOY_DIR/venv/bin/pip" install -r "$DEPLOY_DIR/requirements.txt" --quiet
echo "  venv ready."

echo "=== 3/5  Copy .env for server ==="
if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    cp "$DEPLOY_DIR/deploy/server.env" "$DEPLOY_DIR/.env"
    echo "  .env created from deploy/server.env — verify the credentials."
else
    echo "  .env already exists — skipping."
fi

echo "=== 4/5  Install systemd units ==="
sudo cp "$DEPLOY_DIR/deploy/compasia-sync.service" "$SYSTEMD_DIR/"
sudo cp "$DEPLOY_DIR/deploy/compasia-sync.timer"   "$SYSTEMD_DIR/"
chmod +x "$DEPLOY_DIR/deploy/run_sync.sh"
sudo systemctl daemon-reload

echo "=== 5/5  Enable and start timer ==="
sudo systemctl enable compasia-sync.timer
sudo systemctl start  compasia-sync.timer

echo ""
echo "Done. Scheduled runs:"
systemctl list-timers compasia-sync.timer --no-pager

echo ""
echo "To run immediately:    sudo systemctl start compasia-sync.service"
echo "To watch logs:         journalctl -u compasia-sync -f"
echo "To check timer status: systemctl status compasia-sync.timer"
