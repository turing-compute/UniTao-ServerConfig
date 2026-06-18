#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="unitao-kvm-host"
TEMPLATE_SERVICE="$SCRIPT_DIR/${SERVICE_NAME}.service"
DEFAULT_CONFIG="$REPO_ROOT/src/rest/config.json"
SYSTEMD_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"

# Runtime directory parameter — service files and config live here, independent of the repo.
if [ -z "$1" ]; then
    echo "ERROR: Missing required argument: runtime directory path"
    echo "Usage: $0 <runtime-dir>"
    echo "Example: $0 /opt/run/UniTao-ServerConfig/"
    exit 1
fi
RUNTIME_DIR="$1"
CONFIG_FILE="$RUNTIME_DIR/config.json"
DEPLOYED_SERVICE="$RUNTIME_DIR/${SERVICE_NAME}.service"

# Auto-detect host IPv4 address for hostApiUrl.
HOST_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[\d.]+' | head -1)
if [ -z "$HOST_IP" ]; then
    # Fallback: pick first global IPv4 address.
    HOST_IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | head -1)
fi
if [ -z "$HOST_IP" ]; then
    echo "ERROR: Cannot detect host IP address. Set HOST_IP env var and retry."
    exit 1
fi
HOST_API_URL="http://${HOST_IP}:5000"

echo "=== Deploying ${SERVICE_NAME} ==="
echo "Runtime dir: $RUNTIME_DIR"
echo "Repo root:   $REPO_ROOT"
echo "Host IP:     $HOST_IP"

# 1. Create runtime directory.
sudo mkdir -p "$RUNTIME_DIR"

# 2. Generate host key pair for VM secure access.
KEY_DIR="$RUNTIME_DIR/keys"
if [ ! -f "$KEY_DIR/host_private_key.pem" ]; then
    echo "Generating host key pair (RSA 4096)..."
    sudo "$REPO_ROOT/src/runpy.sh" "$REPO_ROOT/src/security/generate_keys.py" "$KEY_DIR"
    sudo chmod 600 "$KEY_DIR/host_private_key.pem"
    echo "Key pair generated in $KEY_DIR"
else
    echo "Host key pair already exists in $KEY_DIR"
fi

# 3. Copy service file to runtime dir and fix paths.
sudo cp "$TEMPLATE_SERVICE" "$DEPLOYED_SERVICE"
sudo sed -i "s|{{REPO_ROOT}}|$REPO_ROOT|g" "$DEPLOYED_SERVICE"
sudo sed -i "s|{{CONFIG_DIR}}|$RUNTIME_DIR|g" "$DEPLOYED_SERVICE"
echo "Service file installed to $DEPLOYED_SERVICE"

# 4. Copy default config.json if not already present.
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config not found, copying default..."
    sudo cp "$DEFAULT_CONFIG" "$CONFIG_FILE"
    echo "Default config copied to $CONFIG_FILE"
else
    echo "Config already exists at $CONFIG_FILE"
fi

# Ensure hostKeyDir matches the key pair generated in step 2.
sudo sed -i "s|\"hostKeyDir\": \".*\"|\"hostKeyDir\": \"$KEY_DIR\"|" "$CONFIG_FILE"
echo "Config hostKeyDir set to $KEY_DIR"

# Ensure hostApiUrl is set to the detected host IP (may be missing from older configs).
if grep -q '"hostApiUrl"' "$CONFIG_FILE"; then
    sudo sed -i "s|\"hostApiUrl\": \".*\"|\"hostApiUrl\": \"$HOST_API_URL\"|" "$CONFIG_FILE"
    echo "Config hostApiUrl updated to $HOST_API_URL"
else
    sudo sed -i "s|\"hostKeyDir\": \"$KEY_DIR\"|\"hostApiUrl\": \"$HOST_API_URL\",\n  \"hostKeyDir\": \"$KEY_DIR\"|" "$CONFIG_FILE"
    echo "Config hostApiUrl set to $HOST_API_URL"
fi

# Remove config attributes not present in the default config template.
sudo python3 -c "
import json
with open('$DEFAULT_CONFIG') as f:
    template = json.load(f)
with open('$CONFIG_FILE') as f:
    target = json.load(f)
extra = [k for k in target if k not in template]
for k in extra:
    del target[k]
if extra:
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(target, f, indent=4)
    print(f'Removed unrecognized config keys: {extra}')
"

# 5. Symlink into systemd.
echo "Linking into systemd..."
sudo ln -sf "$DEPLOYED_SERVICE" "$SYSTEMD_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Done. Status:"
sudo systemctl status "$SERVICE_NAME" --no-pager
