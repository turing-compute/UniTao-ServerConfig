#!/usr/bin/env python3

"""VM Inventory Tool — exchanges data between a VM and the host REST API.

Self-contained (stdlib only). Reads /opt/unitao-server-config/inventory.json
(injected by cloud-init) to discover the host API URL and VM ID.

Usage:
    # Post data to the host
    python3 /opt/unitao-server-config/inventory_tool.py --data /path/to/data.json

    # List all inventory files on the host
    python3 /opt/unitao-server-config/inventory_tool.py --get

    # Get a specific inventory file
    python3 /opt/unitao-server-config/inventory_tool.py --get <filename>

    # Get and save to file
    python3 /opt/unitao-server-config/inventory_tool.py --get <filename> --output /tmp/result.json
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


def get_inventory_list(host_api_url: str, vm_id: str) -> list:
    """GET the list of inventory file names from the host."""
    url = f"{host_api_url}/api/v1/vms/{vm_id}/inventory"
    cmd = ["curl", "-s", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: curl failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        resp = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"ERROR: Invalid response from host: {result.stdout}", file=sys.stderr)
        sys.exit(1)
    if not resp.get("success"):
        error = resp.get("error", {}).get("message", "Unknown error")
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    return resp["data"].get("files", [])


def get_inventory_file(host_api_url: str, vm_id: str, filename: str) -> dict:
    """GET a specific inventory file from the host.
    Returns {"content": ..., "timestamp": "..."}
    """
    url = f"{host_api_url}/api/v1/vms/{vm_id}/inventory/{filename}"
    cmd = ["curl", "-s", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: curl failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        resp = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"ERROR: Invalid response from host: {result.stdout}", file=sys.stderr)
        sys.exit(1)
    if not resp.get("success"):
        error = resp.get("error", {}).get("message", "Unknown error")
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    data = resp["data"]
    return {"content": data["content"], "timestamp": data.get("timestamp", "")}


def main():
    parser = argparse.ArgumentParser(description="VM Inventory Tool")
    parser.add_argument("--data", type=str,
                        help="Path to JSON data file to post to host")
    parser.add_argument("--get", type=str, nargs="?", const=True,
                        help="Get inventory data. Without argument lists all files. "
                             "With argument gets a specific file by name.")
    parser.add_argument("--output", type=str,
                        help="Save output to file instead of stdout")
    args = parser.parse_args()

    if not args.data and args.get is None:
        parser.print_help()
        print("\nERROR: Either --data or --get is required.", file=sys.stderr)
        sys.exit(1)

    config = load_inventory_config()
    host_api_url = config.get("hostApiUrl")
    vm_id = config.get("vmId")

    if not host_api_url or not vm_id:
        print("ERROR: Invalid inventory config: missing hostApiUrl or vmId",
              file=sys.stderr)
        sys.exit(1)

    print(f"VM: {vm_id}, Host API: {host_api_url}")

    # ── POST mode ──
    if args.data:
        post_data(args.data, host_api_url, vm_id)
        return

    # ── GET mode ──
    if args.get is True:
        # List all inventory files.
        files = get_inventory_list(host_api_url, vm_id)
        output_lines = [f"Inventory files for {vm_id} ({len(files)}):"]
        for f in files:
            output_lines.append(f"  {f}")
        output_text = "\n".join(output_lines)
        if not files:
            output_text += "\n  (none)"
    else:
        # Get specific file.
        result = get_inventory_file(host_api_url, vm_id, args.get)
        ts = result.get("timestamp", "")
        if ts:
            print(f"Last modified: {ts}")
        output_text = json.dumps(result["content"], indent=4)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_text + "\n")
        print(f"Saved to {args.output}")
    else:
        print(output_text)


if __name__ == "__main__":
    main()
