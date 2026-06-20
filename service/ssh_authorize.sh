#!/bin/bash
#
# Manage SSH authorized keys from a PEM-format public key file.
#
# Usage:
#   ./src/service/ssh_authorize.sh add    <pub_key.pem> [authorized_keys_path]
#   ./src/service/ssh_authorize.sh remove <pub_key.pem> [authorized_keys_path]
#
# Default authorized_keys path: ~/.ssh/authorized_keys

set -e

ACTION="$1"
PEM_FILE="$2"
AUTH_KEYS="${3:-$HOME/.ssh/authorized_keys}"

if [ $# -lt 2 ]; then
    echo "Usage: $0 {add|remove} <pub_key.pem> [authorized_keys_path]"
    exit 1
fi

if [ "$ACTION" != "add" ] && [ "$ACTION" != "remove" ]; then
    echo "ERROR: action must be 'add' or 'remove', got '$ACTION'"
    exit 1
fi

if [ ! -f "$PEM_FILE" ]; then
    echo "ERROR: public key file not found: $PEM_FILE"
    exit 1
fi

# Compute fingerprint from PEM for reliable matching.
FINGERPRINT=$(ssh-keygen -l -f "$PEM_FILE" 2>/dev/null | awk '{print $2}')
if [ -z "$FINGERPRINT" ]; then
    echo "ERROR: failed to compute fingerprint for $PEM_FILE"
    exit 1
fi

# Ensure .ssh directory and authorized_keys exist.
mkdir -p "$(dirname "$AUTH_KEYS")"
touch "$AUTH_KEYS"

case "$ACTION" in
    add)
        if ssh-keygen -l -f "$AUTH_KEYS" 2>/dev/null | grep -qF "$FINGERPRINT"; then
            echo "Key already present in $AUTH_KEYS (skipped)"
        else
            OPENSSH_KEY=$(ssh-keygen -i -m PKCS8 -f "$PEM_FILE")
            echo "$OPENSSH_KEY" >> "$AUTH_KEYS"
            echo "Key added to $AUTH_KEYS"
        fi
        ;;
    remove)
        if ssh-keygen -l -f "$AUTH_KEYS" 2>/dev/null | grep -qF "$FINGERPRINT"; then
            LINE_NUM=$(ssh-keygen -l -f "$AUTH_KEYS" 2>/dev/null | grep -nF "$FINGERPRINT" | head -1 | cut -d: -f1)
            if [ -n "$LINE_NUM" ]; then
                TMPFILE=$(mktemp)
                sed "${LINE_NUM}d" "$AUTH_KEYS" > "$TMPFILE"
                mv "$TMPFILE" "$AUTH_KEYS"
                echo "Key removed from $AUTH_KEYS"
            fi
        else
            echo "Key not found in $AUTH_KEYS (skipped)"
        fi
        ;;
esac
