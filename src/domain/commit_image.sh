#!/usr/bin/env bash
#
# commit_image.sh — Commit a prepared VM image on a KVM host.
#
# Usage:
#     ./commit_image.sh -d <domain> -v <vm_name> [-k <ssh_key>] [-u <user>]
#
# Example:
#     ./commit_image.sh -d wireguard -v wireguard-prep -k /opt/run/UniTao-ServerConfig/keys/host_private_key.pem
#
set -euo pipefail

HOST="http://localhost:5000"
SSH_USER="ubuntu"
SSH_KEY=""
DOMAIN=""
VM_NAME=""
DOMAIN_PREP="/opt/unitao/domain/{domain}/prep_image_for_commit.py"
VM_PREP="/opt/unitao-server-config/prep_image_for_commit.py"

usage() {
    echo "Usage: $0 -d <domain> -v <vm_name> [-k <ssh_key>] [-u <user>]"
    exit 1
}

while getopts "d:v:k:u:" opt; do
    case $opt in
        d) DOMAIN="$OPTARG" ;;
        v) VM_NAME="$OPTARG" ;;
        k) SSH_KEY="$OPTARG" ;;
        u) SSH_USER="$OPTARG" ;;
        *) usage ;;
    esac
done

if [ -z "$DOMAIN" ] || [ -z "$VM_NAME" ]; then
    usage
fi

SSH_CMD="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"
if [ -n "$SSH_KEY" ]; then
    SSH_CMD="$SSH_CMD -i $SSH_KEY"
fi

DOMAIN_PREP_PATH="${DOMAIN_PREP/\{domain\}/$DOMAIN}"

echo "=== Commit Image ==="
echo "  Domain:    $DOMAIN"
echo "  VM:        $VM_NAME"
echo "  Prep:      $DOMAIN_PREP_PATH"
echo ""

# ── 1. Get VM IP ─────────────────────────────────────────────────────

echo "[1/5] Getting VM IP ..."
VM_IP=$(curl -s "$HOST/api/v1/vms/$VM_NAME/inventory/network-info.json" | \
    python3 -c "
import sys, json
resp = json.load(sys.stdin)
content = resp.get('data', {}).get('content', {})
if isinstance(content, dict) and 'data' in content:
    content = content['data']
for iface in content.get('interfaces', []):
    ip = iface.get('ip', '')
    if ip:
        print(ip)
        break
")
if [ -z "$VM_IP" ]; then
    echo "ERROR: no IP found for VM '$VM_NAME'"
    exit 1
fi
echo "  IP: $VM_IP"

# ── 2. Run domain prep ───────────────────────────────────────────────

echo ""
echo "[2/5] Running domain prep ($DOMAIN) ..."
$SSH_CMD ${SSH_USER}@${VM_IP} sudo python3 "$DOMAIN_PREP_PATH" --force || true

# ── 3. Run VM-level prep ─────────────────────────────────────────────

echo "sleep 2"
sleep 2
echo "[3/5] Running VM prep ..."
$SSH_CMD ${SSH_USER}@${VM_IP} sudo python3 "$VM_PREP" --force || true

# ── 4. Stop VM via REST API ──────────────────────────────────────────
echo "sleep 2"
sleep 2
echo "[4/5] Stopping VM ..."
curl -s -X POST "$HOST/api/v1/vms/$VM_NAME/stop" -H "Content-Type: application/json" > /dev/null

# Wait for virsh state to go to shutOff / notExists.
for i in $(seq 1 30); do
    sleep 5
    STATE=$(curl -s "$HOST/api/v1/vms/$VM_NAME" | \
        python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('virshState',''))")
    if [ "$STATE" = "notExists" ] || [ "$STATE" = "shutOff" ]; then
        echo "  VM stopped. ($((i * 5))s)"
        break
    fi
    printf "."
done
echo ""

# ── 5. Commit and delete ─────────────────────────────────────────────
echo "sleep 2"
sleep 2
echo "  Committing image ..."
curl -s -X POST "$HOST/api/v1/vms/$VM_NAME/commit" -H "Content-Type: application/json" \
    -d '{}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('message',''))"

echo "sleep 2"
sleep 2
echo "  Deleting VM '$VM_NAME' ..."
curl -s -X DELETE "$HOST/api/v1/vms/$VM_NAME" > /dev/null
echo "  Deleted."

echo ""
echo "=== Done ==="
echo "  Image committed. VM '$VM_NAME' removed."
