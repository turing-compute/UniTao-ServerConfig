#!/usr/bin/env python3

"""VM Inventory Tool — posts data from inside a VM to the host REST API.

Reads /opt/unitao-server-config/inventory.json (injected by cloud-init) to
discover the host API URL and VM ID, then POSTs a JSON file to the host's
inventory endpoint.

Usage:
    ./src/runpy.sh src/security/inventory_tool.py --data /path/to/data.json
"""

import argparse
import json
import logging
import os
import sys

from shared.logger import Log
from shared.utilities import Util

INVENTORY_CONFIG_PATH = "/opt/unitao-server-config/inventory.json"


def load_inventory_config(config_path: str = INVENTORY_CONFIG_PATH) -> dict:
    """Load the inventory config injected by cloud-init."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"Inventory config not found at {config_path}. "
            "Ensure the VM was created with shareInventoryData=true."
        )
    return Util.read_json_file(config_path)


def post_data(data_path: str, host_api_url: str, vm_id: str, logger: logging.Logger):
    """POST a JSON file to the host inventory endpoint."""
    data = Util.read_json_file(data_path)
    url = f"{host_api_url}/api/v1/vms/{vm_id}/inventory"

    # Build curl command since we don't have requests library.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(data, tmp)
        tmp_path = tmp.name

    try:
        cmd = f"curl -s -X POST {url} -H \"Content-Type: application/json\" -d @{tmp_path}"
        logger.info(f"Posting inventory data to {url}")
        Util.run_command(cmd)
        logger.info("Inventory data posted successfully")
    finally:
        os.unlink(tmp_path)


def main():
    parser = argparse.ArgumentParser(description="VM Inventory Tool")
    parser.add_argument("--data", type=str, required=True,
                        help="Path to JSON data file to post")
    args = parser.parse_args()

    logger = Log.get_logger("InventoryTool")

    config = load_inventory_config()
    host_api_url = config.get("hostApiUrl")
    vm_id = config.get("vmId")

    if not host_api_url or not vm_id:
        logger.error("Invalid inventory config: missing hostApiUrl or vmId")
        sys.exit(1)

    logger.info(f"VM: {vm_id}, Host API: {host_api_url}")
    post_data(args.data, host_api_url, vm_id, logger)


if __name__ == "__main__":
    main()
