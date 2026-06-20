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

# Convert PEM to OpenSSH single-line format.
OPENSSH_KEY=$(ssh-keygen -i -m PKCS8 -f "$PEM_FILE" 2>/dev/null)
if [ -z "$OPENSSH_KEY" ]; then
    echo "ERROR: failed to convert $PEM_FILE to OpenSSH format"
    exit 1
fi

# Ensure .ssh directory and authorized_keys exist.
mkdir -p "$(dirname "$AUTH_KEYS")"
touch "$AUTH_KEYS"

case "$ACTION" in
    add)
        if grep -qF "$OPENSSH_KEY" "$AUTH_KEYS"; then
            echo "Key already present in $AUTH_KEYS (skipped)"
        else
            echo "$OPENSSH_KEY" >> "$AUTH_KEYS"
            echo "Key added to $AUTH_KEYS"
        fi
        ;;
    remove)
        if grep -qF "$OPENSSH_KEY" "$AUTH_KEYS"; then
            TMPFILE=$(mktemp)
            grep -vF "$OPENSSH_KEY" "$AUTH_KEYS" > "$TMPFILE"
            mv "$TMPFILE" "$AUTH_KEYS"
            echo "Key removed from $AUTH_KEYS"
        else
            echo "Key not found in $AUTH_KEYS (skipped)"
        fi
        ;;
esac
