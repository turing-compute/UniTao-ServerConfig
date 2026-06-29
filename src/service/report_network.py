#!/usr/bin/env python3

"""Collect VM network configuration and report to host inventory.

Used at VM boot (via cloud-init runcmd) when shareInventoryData=true.
Generates network-info.json, then calls inventory_tool.py to post it.

Usage:
    python3 /opt/unitao-server-config/report_network.py
"""

import json
import os
import re
import subprocess
import sys
import time

INVENTORY_TOOL = "/opt/unitao-server-config/inventory_tool.py"
REPORT_FILE = "/tmp/inventory_network_report.json"
MAX_RETRIES = 10
RETRY_INTERVAL = 6  # seconds


def collect_network_info() -> dict:
    """Collect IP addresses, default route, DNS."""
    info = {"interfaces": [], "defaultRoute": None, "dns": []}

    # ip addr show (JSON output)
    result = subprocess.run(["ip", "-j", "addr", "show"], capture_output=True, text=True)
    if result.returncode == 0:
        try:
            addrs = json.loads(result.stdout)
            for iface in addrs:
                ifname = iface.get("ifname", "")
                if ifname == "lo":
                    continue
                addr_info = iface.get("addr_info", [])
                for a in addr_info:
                    if a.get("family") == "inet":
                        info["interfaces"].append({
                            "name": ifname,
                            "ip": a.get("local", ""),
                            "prefix": a.get("prefixlen", 24),
                            "mac": iface.get("address", ""),
                        })
        except json.JSONDecodeError:
            pass

    # Default route
    result = subprocess.run(["ip", "-j", "route", "show", "default"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        try:
            routes = json.loads(result.stdout)
            if routes:
                info["defaultRoute"] = routes[0].get("gateway", "")
        except json.JSONDecodeError:
            pass

    # DNS from resolv.conf
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                m = re.match(r"^nameserver\s+(\S+)", line)
                if m:
                    info["dns"].append(m.group(1))
    except FileNotFoundError:
        pass

    return info


def main():
    if not os.path.isfile(INVENTORY_TOOL):
        print(f"WARNING: {INVENTORY_TOOL} not found, skipping network report",
              file=sys.stderr)
        return

    # Retry loop: network may not be ready when cloud-init runcmd fires.
    for attempt in range(1, MAX_RETRIES + 1):
        info = collect_network_info()
        if info.get("interfaces") and info["interfaces"][0].get("ip"):
            break
        print(f"Waiting for network (attempt {attempt}/{MAX_RETRIES})...")
        time.sleep(RETRY_INTERVAL)
    else:
        print("WARNING: no IP after retries, reporting anyway", file=sys.stderr)

    report = {"name": "network-info", "data": info}
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f)

    subprocess.run([sys.executable, INVENTORY_TOOL, "--data", REPORT_FILE], check=False)
    print("Network config reported to inventory")

    if os.path.isfile(REPORT_FILE):
        os.remove(REPORT_FILE)


if __name__ == "__main__":
    main()
