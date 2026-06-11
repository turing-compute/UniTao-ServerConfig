#!/bin/bash

# Check if at least one argument (the file path) is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <file-path> [command] [args...]"
    exit 1
fi

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# $(dirname "${BASH_SOURCE[0]}"): Gets the directory of the current script (BASH_SOURCE[0] is the path to the script).
# $(cd ... && pwd): Converts it to an absolute path.

# Set PYTHONPATH to the current directory
export PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/extlib"


# Run the command passed as parameters
python3 "$@"
