#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="unitiao-kvm-host"
TEMPLATE_SERVICE="$SCRIPT_DIR/${SERVICE_NAME}.service"
DEFAULT_CONFIG="$REPO_ROOT/src/rest/config.json"
SYSTEMD_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"

# Runtime directory parameter — service files and config live here, independent of the repo.
RUNTIME_DIR="${1:-/opt/unitiao}"
CONFIG_FILE="$RUNTIME_DIR/config.json"
DEPLOYED_SERVICE="$RUNTIME_DIR/${SERVICE_NAME}.service"

echo "=== Deploying ${SERVICE_NAME} ==="
echo "Runtime dir: $RUNTIME_DIR"
echo "Repo root:   $REPO_ROOT"

# 1. Create runtime directory.
sudo mkdir -p "$RUNTIME_DIR"

# 2. Copy service file to runtime dir and fix paths.
sudo cp "$TEMPLATE_SERVICE" "$DEPLOYED_SERVICE"
sudo sed -i "s|{{REPO_ROOT}}|$REPO_ROOT|g" "$DEPLOYED_SERVICE"
sudo sed -i "s|{{CONFIG_DIR}}|$RUNTIME_DIR|g" "$DEPLOYED_SERVICE"
echo "Service file installed to $DEPLOYED_SERVICE"

# 3. Copy default config.json if not already present.
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config not found, copying default..."
    sudo cp "$DEFAULT_CONFIG" "$CONFIG_FILE"
    echo "Default config copied to $CONFIG_FILE"
else
    echo "Config already exists at $CONFIG_FILE"
fi

# 4. Symlink into systemd.
echo "Linking into systemd..."
sudo ln -sf "$DEPLOYED_SERVICE" "$SYSTEMD_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Done. Status:"
sudo systemctl status "$SERVICE_NAME" --no-pager
