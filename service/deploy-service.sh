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

# 0. Dependencies — each installer runs only when something it provides is
#    missing or outdated, so fresh and update deploys both just run this script.
#    a. System packages (src/kvm_install.sh): any required binary or noVNC missing.
MISSING_SYS=()
for cmd in virsh virt-install qemu-img genisoimage brctl ovs-vsctl websockify; do
    command -v "$cmd" >/dev/null 2>&1 || MISSING_SYS+=("$cmd")
done
[ -f /usr/share/novnc/core/rfb.js ] || MISSING_SYS+=("novnc")
if [ "${#MISSING_SYS[@]}" -gt 0 ]; then
    echo "Missing system dependencies: ${MISSING_SYS[*]} — running kvm_install.sh"
    sudo "$REPO_ROOT/src/kvm_install.sh"
else
    echo "System dependencies present"
fi

#    b. Python packages (src/req_install.sh → src/extlib): requirements.txt
#       changed since the last install, or never installed.
REQ_FILE="$REPO_ROOT/src/requirements.txt"
REQ_STAMP="$REPO_ROOT/src/extlib/.requirements.sha256"
REQ_SHA=$(sha256sum "$REQ_FILE" | cut -d' ' -f1)
if [ ! -f "$REQ_STAMP" ] || [ "$(cat "$REQ_STAMP")" != "$REQ_SHA" ]; then
    echo "Python requirements not installed or changed — running req_install.sh"
    sudo "$REPO_ROOT/src/req_install.sh"
else
    echo "Python requirements up to date"
fi

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

# 4. Create or reconcile config.json against the default template:
#    - missing file → copy the template;
#    - keys the template has but the config lacks → asked interactively
#      (Enter accepts the template default; defaults are used without a terminal);
#    - keys not in the template → removed;
#    - hostKeyDir / hostApiUrl → always set to the values detected above.
sudo python3 "$SCRIPT_DIR/merge_config.py" \
    --template "$DEFAULT_CONFIG" \
    --config "$CONFIG_FILE" \
    --set "hostKeyDir=$KEY_DIR" \
    --set "hostApiUrl=$HOST_API_URL"

# 5. Symlink into systemd.
echo "Linking into systemd..."
sudo ln -sf "$DEPLOYED_SERVICE" "$SYSTEMD_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Done. Status:"
sudo systemctl status "$SERVICE_NAME" --no-pager
