#!/bin/bash
set -e

# Resolve this script's own directory, so the relative paths below work no
# matter from where the script is invoked (repo root or src/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Debian/Ubuntu only provide pip through the python3-pip package, so it may be
# missing (e.g. when this script runs before kvm_install.sh). Auto-install it
# when we have root; otherwise fail with a clear hint.
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found. install it first, e.g.:" >&2
    echo "    sudo apt-get install -y python3" >&2
    exit 1
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "error: python3-pip is not installed, and installing it needs root." >&2
        echo "re-run with sudo, or install it manually:" >&2
        echo "    sudo apt-get install -y python3-pip" >&2
        exit 1
    fi
    echo "python3-pip not found; installing it via apt-get..."
    apt-get update
    apt-get install -y python3-pip
fi

# use python3 -m pip instead of the bare "pip" command so it does not depend on
# PATH resolution (which sudo resets) or on the pip binary name
python3 -m pip install -r ./requirements.txt --target ./extlib/
