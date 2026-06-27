#!/usr/bin/env python3

"""WireGuard Agent 的 systemd 服务管理。

安装、卸载、状态检查。可在 image 构建时或运行时使用。
网络名称从 /opt/unitao/wg_network.json 自动读取，无需传参。

用法:
    python3 wg_agent_service.py install
    python3 wg_agent_service.py status
    python3 wg_agent_service.py uninstall
"""

import argparse
import json
import os
import subprocess
import sys

from domain.wireguard.wg_key_manager import WgKeyManager

SYSTEMD_DIR = "/etc/systemd/system"
AGENT_DIR = "/opt/unitao"
AGENT_CONFIG_PATH = os.path.join(AGENT_DIR, "wg_agent.conf")

UNIT_NAME = "wg-agent.service"

UNIT_TEMPLATE = """[Unit]
Description=WireGuard Mesh Agent ({network})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 {agent_dir}/wg_agent.py
ExecStop=/usr/bin/wg-quick down {network}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _unit_path() -> str:
    return os.path.join(SYSTEMD_DIR, UNIT_NAME)


def _run_systemctl(*args) -> tuple:
    """Run systemctl, return (success, output)."""
    try:
        result = subprocess.run(
            ["systemctl"] + list(args),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        return False, "systemctl not found"


def _read_network_name() -> str | None:
    """从 agent config → networkConfigPath 读取 networkName。"""
    if not os.path.isfile(AGENT_CONFIG_PATH):
        return None
    try:
        with open(AGENT_CONFIG_PATH, "r") as f:
            agent_conf = json.load(f)
        network_config_path = agent_conf.get("networkConfigPath", None)
        if not network_config_path or not os.path.isfile(network_config_path):
            return None
        with open(network_config_path, "r") as f:
            network_conf = json.load(f)
        return network_conf.get("networkName", None)
    except (json.JSONDecodeError, OSError):
        return None


def install():
    """安装并启用 systemd 服务单元。

    从 /opt/unitao/wg_network.json 读取 networkName。
    """
    network = _read_network_name()
    if not network:
        print(f"ERROR: Cannot determine network name.", file=sys.stderr)
        print(f"  Make sure {AGENT_CONFIG_PATH} exists and networkConfigPath points to a valid WgNetworkConfig.",
              file=sys.stderr)
        sys.exit(1)

    unit_path = _unit_path()
    print(f"=== Installing wg-agent service [{network}] ===\n")

    # 1. 创建 unit 文件
    os.makedirs(SYSTEMD_DIR, exist_ok=True)
    unit_content = UNIT_TEMPLATE.format(
        network=network, agent_dir=AGENT_DIR
    )
    with open(unit_path, "w") as f:
        f.write(unit_content)
    print(f"  Created: {unit_path}")

    # 2. daemon-reload
    ok, _ = _run_systemctl("daemon-reload")
    if not ok:
        print(f"  [WARN] systemctl daemon-reload failed", file=sys.stderr)
    else:
        print(f"  daemon-reload OK")

    # 3. enable + start
    ok, _ = _run_systemctl("enable", "--now", UNIT_NAME)
    if ok:
        print(f"  Service enabled and started.")
    else:
        print(f"  [WARN] systemctl enable --now failed", file=sys.stderr)

    print(f"\n=== Install complete ===")


def uninstall():
    """停止、禁用并删除 systemd 服务单元。"""
    network = _read_network_name()
    unit_path = _unit_path()
    print(f"=== Uninstalling wg-agent service{f' [{network}]' if network else ''} ===\n")

    # 1. stop
    ok, _ = _run_systemctl("stop", UNIT_NAME)
    if ok:
        print(f"  Service stopped.")
    else:
        print(f"  Service not running (or already stopped).")

    # 2. disable
    ok, _ = _run_systemctl("disable", UNIT_NAME)
    if ok:
        print(f"  Service disabled.")
    else:
        print(f"  Service not enabled (or already disabled).")

    # 3. daemon-reload
    _run_systemctl("daemon-reload")

    # 4. 删除 unit 文件
    if os.path.isfile(unit_path):
        os.remove(unit_path)
        print(f"  Removed: {unit_path}")
    else:
        print(f"  Unit file not found: {unit_path}")

    # 5. down 接口 (如果还在运行)
    if network:
        km = WgKeyManager(network)
        if km.interface_exists(network):
            try:
                subprocess.run(
                    ["wg-quick", "down", network],
                    capture_output=True, text=True,
                )
                print(f"  Interface [{network}] brought down.")
            except FileNotFoundError:
                pass

    print(f"\n=== Uninstall complete ===")


def status():
    """检查 agent 服务和 WireGuard 接口状态。"""
    network = _read_network_name()
    print(f"=== wg-agent service status{f' [{network}]' if network else ''} ===\n")

    # systemd 状态
    ok, out = _run_systemctl("is-active", UNIT_NAME)
    if out == "active":
        print(f"  Service:    active")
    elif out == "inactive":
        print(f"  Service:    inactive")
    elif out == "failed":
        print(f"  Service:    FAILED")
    else:
        print(f"  Service:    unknown ({out})")

    ok, out = _run_systemctl("is-enabled", UNIT_NAME)
    print(f"  Enabled:    {out}")

    # unit 文件
    unit_path = _unit_path()
    print(f"  Unit file:  {unit_path} {'(exists)' if os.path.isfile(unit_path) else '(not found)'}")
    print(f"  Config:     {AGENT_CONFIG_PATH} {'(exists)' if os.path.isfile(AGENT_CONFIG_PATH) else '(not found)'}")
    if os.path.isfile(AGENT_CONFIG_PATH):
        try:
            with open(AGENT_CONFIG_PATH, "r") as f:
                ac = json.load(f)
            nc = ac.get("networkConfigPath", "?")
            print(f"  Network:    {nc} {'(exists)' if os.path.isfile(nc) else '(not found)'}")
        except Exception:
            pass

    # WireGuard 接口
    if network:
        km = WgKeyManager(network)
        if km.interface_exists(network):
            pubkey = km.get_interface_public_key(network)
            print(f"  Interface:  UP [{network}]")
            if pubkey:
                print(f"  PublicKey:  {pubkey}")
        else:
            print(f"  Interface:  DOWN [{network}]")
        print(f"  Keys exist: {'yes' if km.keys_exist() else 'no'}")
    else:
        print(f"  Network:    unknown (config not found)")

    print()


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="WireGuard Agent systemd service management"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("install", help="Install and enable the service")
    sub.add_parser("uninstall", help="Stop, disable, and remove the service")
    sub.add_parser("status", help="Show service and interface status")

    args = parser.parse_args()

    if args.command == "install":
        install()
    elif args.command == "uninstall":
        uninstall()
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
