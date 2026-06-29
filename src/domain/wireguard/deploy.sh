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
#   ./deploy.sh <vm_ip> [ssh_user] [--ssh-key <path>]
#
#   vm_ip     — Target VM IP address (required)
#   ssh_user  — SSH user (default: ubuntu)
#   --ssh-key — Path to SSH private key file (optional, passed to ssh -i)
#
# Example:
#   ./deploy.sh 192.168.1.104
#   ./deploy.sh 192.168.1.104 root
#   ./deploy.sh 192.168.1.104 ubuntu --ssh-key ~/.ssh/host_key
#
# Prerequisites:
#   - SSH key loaded in ssh-agent (for HostKey-auth VMs)
#   - Target VM must have Python 3 installed
#
set -euo pipefail

VM_IP="${1:?Usage: $0 <vm_ip> [ssh_user] [--ssh-key <path>]}"
SSH_USER="${2:-ubuntu}"

# Parse --ssh-key from remaining args (skip VM_IP and SSH_USER)
SSH_KEY=""
shift 2 2>/dev/null || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

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

FILES="wg_data.py wg_agent.py wg_config_builder.py wg_key_manager.py wg_agent_service.py install.py prep_image_for_commit.py"
for f in $FILES; do
    [ -f "/tmp/$f" ] && sudo mv "/tmp/$f" "/opt/unitao/domain/wireguard/$f"
done

echo "  Files moved to /opt/unitao/domain/wireguard/"
sudo chmod +x /opt/unitao/domain/wireguard/*.py

echo "  Verifying Python syntax ..."
sudo rm -rf /opt/unitao/domain/wireguard/__pycache__
for f in $FILES; do
    if ! sudo python3 -m py_compile "/opt/unitao/domain/wireguard/$f" 2>/dev/null; then
        echo "  ERROR: $f has syntax errors" >&2
        sudo python3 -m py_compile "/opt/unitao/domain/wireguard/$f" 2>&1
        exit 1
    fi
    echo "    $f OK"
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
echo "Starting agent ..."
ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" "sudo systemctl start wg-agent"
sleep 3
ssh ${SSH_OPTS} "${SSH_USER}@${VM_IP}" "systemctl is-active wg-agent && echo 'wg-agent is active' || echo 'WARN: wg-agent not active'"

echo ""
echo "=== Deploy complete ==="
echo ""
echo "  Agent is running. Verify it published wireguard_network.json:"
echo "    curl -s \$HOST/api/v1/vms/<name>/inventory | python3 -m json.tool"
echo ""
echo "  Next steps:"
echo ""
echo "  Orchestrator: Post updated config via REST API:"
echo "    POST /api/v1/vms/<name>/inventory"
echo '    Body: {"name":"wireguard_network","timestamp":"...","data":{"network":{...}}}'
