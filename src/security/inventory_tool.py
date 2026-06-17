#!/usr/bin/env python3

"""VM Inventory Tool — posts data from inside a VM to the host REST API.

Self-contained (stdlib only). Reads /opt/unitao-server-config/inventory.json
(injected by cloud-init) to discover the host API URL and VM ID, then POSTs a
JSON file to the host's inventory endpoint.

Usage:
    python3 /opt/unitao-server-config/inventory_tool.py --data /path/to/data.json
"""

import argparse
import json
import os
import subprocess
import sys

INVENTORY_CONFIG_PATH = "/opt/unitao-server-config/inventory.json"


def load_inventory_config(config_path: str = INVENTORY_CONFIG_PATH) -> dict:
    """Load the inventory config injected by cloud-init."""
    if not os.path.exists(config_path):
        print(f"ERROR: Inventory config not found at {config_path}. "
              "Ensure the VM was created with shareInventoryData=true.",
              file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r") as f:
        return json.load(f)


def post_data(data_path: str, host_api_url: str, vm_id: str):
    """POST a JSON file to the host inventory endpoint."""
    url = f"{host_api_url}/api/v1/vms/{vm_id}/inventory"

    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "-d", f"@{data_path}",
    ]
    print(f"Posting {data_path} to {url}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: curl failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description="VM Inventory Tool")
    parser.add_argument("--data", type=str, required=True,
                        help="Path to JSON data file to post")
    args = parser.parse_args()

    config = load_inventory_config()
    host_api_url = config.get("hostApiUrl")
    vm_id = config.get("vmId")

    if not host_api_url or not vm_id:
        print("ERROR: Invalid inventory config: missing hostApiUrl or vmId",
              file=sys.stderr)
        sys.exit(1)

    print(f"VM: {vm_id}, Host API: {host_api_url}")
    post_data(args.data, host_api_url, vm_id)


if __name__ == "__main__":
    main()
