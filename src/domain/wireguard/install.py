#!/usr/bin/env python3

"""Install wg_agent systemd service into the VM image.

Run this during image preparation (before prep_image_for_commit.py + commit).

Steps:
    1. Install system dependencies (wireguard-tools via apt-get)
    2. Generate wg_agent.conf telling wg_agent.py where everything is
    3. Create and enable the systemd unit

Usage:
    python3 install.py --network-config ./wg-mesh.json
"""

import argparse
import json
import os
import subprocess
import sys

SYSTEMD_DIR = "/etc/systemd/system"
AGENT_DIR = "/opt/unitao"
DEFAULT_WG_DIR = "/etc/wireguard"
DEFAULT_INVENTORY_TOOL = "/opt/unitao-server-config/inventory_tool.py"

UNIT_NAME = "wg-agent.service"
AGENT_CONFIG_NAME = "wg_agent.conf"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def read_network_name(network_config_path: str) -> str:
    """Return the WireGuard interface name.

    FIXME: Hardcoded to "wg0" for now.
    In the future, the network name may be stored in the config or
    use "wg{network_idx}" convention for multiple networks.
    """
    return "wg0"


def install_system_deps():
    """Install system packages required by WireGuard (if not already installed)."""
    try:
        result = subprocess.run(
            ["apt-get", "install", "-y", "wireguard-tools"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  [WARN] apt-get install failed: {result.stderr.strip()}",
                  file=sys.stderr)
        else:
            print(f"  wireguard-tools installed.")
    except FileNotFoundError:
        print(f"  [WARN] apt-get not found, skipping system dependency install.",
              file=sys.stderr)


def generate_agent_config(
    network_config_path: str,
    inventory_tool: str,
    wg_dir: str,
) -> str:
    """Generate the agent config file.

    The agent config tells wg_agent.py where to find its data.
    It does NOT contain the WireGuard network config itself (subnet, port, etc.)
    — that stays in the WgNetworkConfig JSON at networkConfigPath.

    Written to /opt/unitao/wg_agent.conf
    """
    config = {
        "networkConfigPath": os.path.abspath(network_config_path),
        "inventoryTool": inventory_tool,
        "wgDir": wg_dir,
    }

    output_path = os.path.join(AGENT_DIR, AGENT_CONFIG_NAME)
    ensure_dir(AGENT_DIR)
    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print(f"  Agent config: {output_path}")
    for k, v in config.items():
        print(f"    {k} = {v}")

    return output_path


def install_systemd_unit(network: str):
    """Create the systemd service unit. Network name used in Description/ExecStop."""
    unit_path = os.path.join(SYSTEMD_DIR, UNIT_NAME)
    ensure_dir(SYSTEMD_DIR)
    unit_content = f"""[Unit]
Description=WireGuard Mesh Agent ({network})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYTHONPATH=/opt/unitao
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 {AGENT_DIR}/domain/wireguard/wg_agent.py
ExecStop=/usr/bin/systemctl stop wg-quick@{network}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    with open(unit_path, "w") as f:
        f.write(unit_content)
    print(f"  Unit file:  {unit_path}")


def enable_systemd_unit():
    """Enable the service via symlink.

    Creates: multi-user.target.wants/wg-agent.service -> ../wg-agent.service
    """
    wants_dir = os.path.join(SYSTEMD_DIR, "multi-user.target.wants")
    ensure_dir(wants_dir)
    target = os.path.join("..", UNIT_NAME)
    link = os.path.join(wants_dir, UNIT_NAME)
    if not os.path.exists(link):
        os.symlink(target, link)
        print(f"  Enabled:    {UNIT_NAME} -> {target}")
    else:
        print(f"  Enabled:    {UNIT_NAME} (already)")


def main():
    parser = argparse.ArgumentParser(
        description="Install wg_agent systemd service into VM image"
    )
    parser.add_argument(
        "--network-config", type=str,
        default=os.path.join(AGENT_DIR, "wireguard_network.json"),
        help="Path to WgNetworkConfig JSON (default: /opt/unitao/wireguard_network.json)",
    )
    parser.add_argument(
        "--inventory-tool", type=str, default=DEFAULT_INVENTORY_TOOL,
        help=f"Path to inventory_tool.py (default: {DEFAULT_INVENTORY_TOOL})",
    )
    parser.add_argument(
        "--wg-dir", type=str, default=DEFAULT_WG_DIR,
        help=f"WireGuard data directory (default: {DEFAULT_WG_DIR})",
    )
    args = parser.parse_args()

    network = read_network_name(args.network_config)

    print("=== Installing wg_agent service ===\n")
    print(f"  Network: {network}\n")

    # 1. Install system dependencies.
    print("[1/3] System dependencies ...")
    install_system_deps()

    # 2. Generate agent config (paths only, not the network config itself).
    print("\n[2/3] Agent config ...")
    generate_agent_config(args.network_config, args.inventory_tool, args.wg_dir)

    # 3. Systemd unit + enable.
    print("\n[3/3] Systemd service ...")
    install_systemd_unit(network)
    enable_systemd_unit()

    print(f"\n=== Install complete ===")
    print(f"  Service: {UNIT_NAME}")
    print(f"  Config:  {os.path.join(AGENT_DIR, AGENT_CONFIG_NAME)}")
    print(f"  Next:    run prep_image_for_commit.py, then commit the image.")


if __name__ == "__main__":
    main()
