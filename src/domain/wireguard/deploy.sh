#!/usr/bin/env bash
#
# deploy.sh — Deploy WireGuard agent to a target VM.
#
# Steps:
#   1. Copy agent Python files to VM
#   2. Create directory structure, move files + verify syntax
#   3. Upgrade system packages (reboot VM if needed)
#   4. Run install.py (wireguard-tools + agent config + systemd unit)
#
# Usage:
#   ./deploy.sh <vm_ip> [ssh_user]
#
#   vm_ip     — Target VM IP address (required)
#   ssh_user  — SSH user (default: ubuntu)
#
# Example:
#   ./deploy.sh 192.168.1.104
#   ./deploy.sh 192.168.1.104 root
#   ./deploy.sh 192.168.1.104 ubuntu --no-fix-crlf
#
# Prerequisites:
#   - SSH key loaded in ssh-agent (for HostKey-auth VMs)
#   - Target VM must have Python 3 installed
#
set -euo pipefail

VM_IP="${1:?Usage: $0 <vm_ip> [ssh_user]}"
SSH_USER="${2:-ubuntu}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="/opt/unitao"
AGENT_PKG_DIR="$AGENT_DIR/domain/wireguard"

FILES=(
    wg_data.py
    wg_agent.py
    wg_config_builder.py
    wg_key_manager.py
    wg_agent_service.py
    install.py
    prep_image_for_commit.py
)

echo "=== WireGuard Agent Deploy ==="
echo "  Target: ${SSH_USER}@${VM_IP}"
echo "  Source: ${SRC_DIR}"
echo ""

# ── Step 1: Copy files to VM ───────────────────────────────────────────

echo "[1/4] Copying agent files ..."
for f in "${FILES[@]}"; do
    scp ${SSH_OPTS} "${SRC_DIR}/${f}" "${SSH_USER}@${VM_IP}:/tmp/${f}"
    echo "  ${f} -> /tmp/${f}"
done

# ── Step 2: Create directory structure, move files, verify syntax ───────

echo ""
echo "[2/4] Setting up directory structure ..."
ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" bash << 'REMOTE_SETUP'
sudo mkdir -p /opt/unitao/domain/wireguard
sudo touch /opt/unitao/domain/__init__.py
sudo touch /opt/unitao/domain/wireguard/__init__.py

for f in wg_data.py wg_agent.py wg_config_builder.py wg_key_manager.py \
         wg_agent_service.py install.py prep_image_for_commit.py; do
    if [ -f "/tmp/$f" ]; then
        sudo mv "/tmp/$f" "/opt/unitao/domain/wireguard/$f"
    fi
done

echo "  Files moved to /opt/unitao/domain/wireguard/"
	sudo chmod +x /opt/unitao/domain/wireguard/*.py
for f in wg_data.py wg_agent.py wg_config_builder.py wg_key_manager.py \
         wg_agent_service.py install.py prep_image_for_commit.py; do
    if ! python3 -c "import py_compile; py_compile.compile('/opt/unitao/domain/wireguard/'"$f"', doraise=True)" 2>/dev/null; then
        echo "  ERROR: $f has syntax errors (likely encoding issue during transfer)" >&2
        exit 1
    fi
done
echo "  All files OK."
REMOTE_SETUP

# ── Step 3: System upgrade ───────────────────────────────────────────

echo ""
echo "[3/4] Upgrading system packages ..."
ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y'
if ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" '[ -f /var/run/reboot-required ]'; then
    echo "  Reboot required, restarting VM ..."
    ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" 'sudo reboot' || true
    echo "  Waiting for VM to come back ..."
    sleep 10
    for i in $(seq 1 30); do
        if ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" 'echo ready' 2>/dev/null; then
            echo "  VM is back online."
            break
        fi
        echo "  ... $((i*5))s"
        sleep 5
    done
else
    echo "  No reboot required."
fi

# ── Step 4: Run install.py ───────────────────────────────────────────

echo ""
echo "[4/4] Running install.py ..."
ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" \
    "sudo python3 ${AGENT_PKG_DIR}/install.py --network-config ${AGENT_DIR}/wireguard_network.json"

echo ""
echo "=== Deploy complete ==="
echo ""
echo "  Next steps:"
echo "    1. SSH into VM:   ssh ${SSH_USER}@${VM_IP}"
echo "    2. Start agent:   sudo systemctl start wg-agent"
echo "    3. Check status:  sudo wg show wg0"
echo "    4. Follow logs:   sudo journalctl -u wg-agent -f"
echo ""
echo "  Orchestrator: Post updated config via REST API:"
echo "    POST /api/v1/vms/<name>/inventory"
echo '    Body: {"name":"wireguard_network","timestamp":"...","data":{"network":{...}}}'
