#!/bin/bash

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Set PYTHONPATH to src/ directory
export PYTHONPATH="$SRC_DIR:$SRC_DIR/extlib"

# Run the Flask app
exec python3 "$SCRIPT_DIR/app.py" "$@"
