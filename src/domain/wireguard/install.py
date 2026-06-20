#!/usr/bin/env python3

"""Install wg_agent into the VM image.

Run this during image preparation (before image_prep.py + commit).
Copies the agent script, creates config directory, installs systemd unit.

Usage:
    python3 install.py --network wg-mesh --agent ./wg_agent.py --config ./wg-mesh.json
"""

import argparse
import os
import shutil
import sys

AGENT_DIR = "/opt/unitao"
SYSTEMD_DIR = "/etc/systemd/system"
WIREGUARD_DIR = "/etc/wireguard"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)
    print(f"  created dir: {path}")


def copy_file(src: str, dst: str, chmod: int = None):
    """Copy a file, optionally setting permissions."""
    dst_dir = os.path.dirname(dst)
    if dst_dir:
        ensure_dir(dst_dir)
    shutil.copy2(src, dst)
    if chmod is not None:
        os.chmod(dst, chmod)
    print(f"  copied: {src} → {dst}")


def install_systemd_unit(network: str):
    """Create systemd service unit for the wg_agent."""
    unit_path = os.path.join(SYSTEMD_DIR, f"wg-agent@{network}.service")
    unit_content = f"""[Unit]
Description=WireGuard Mesh Agent for {network}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 {AGENT_DIR}/wg_agent.py --network {network}
ExecStop=/usr/bin/wg-quick down {network}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    ensure_dir(SYSTEMD_DIR)
    with open(unit_path, "w") as f:
        f.write(unit_content)
    print(f"  created: {unit_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Install wg_agent into VM image")
    parser.add_argument("--network", type=str, required=True,
                        help="WireGuard network name (e.g. wg-mesh)")
    parser.add_argument("--agent", type=str, required=True,
                        help="Path to wg_agent.py")
    parser.add_argument("--config", type=str, required=True,
                        help="Path to wg-mesh.json network config")
    parser.add_argument("--service", type=str, default=None,
                        help="Path to wg_agent_service.py (optional)")
    args = parser.parse_args()

    print("=== Installing wg_agent into image ===\n")
    print(f"Network: {args.network}")
    print(f"Target:  {AGENT_DIR}\n")

    # 1. Agent directory + script.
    dst_agent = os.path.join(AGENT_DIR, "wg_agent.py")
    copy_file(args.agent, dst_agent, chmod=0o755)

    # 2. Network config.
    dst_config = os.path.join(AGENT_DIR, f"{args.network}.json")
    copy_file(args.config, dst_config)

    # 3. WireGuard directory (empty, keys generated at boot).
    ensure_dir(os.path.join(WIREGUARD_DIR, args.network))

    # 4. systemd unit.
    install_systemd_unit(args.network)

    print(f"\n=== Install complete ===")
    print("Next: run image_prep.py to clean VM state, then commit the image.")


if __name__ == "__main__":
    main()
