#!/usr/bin/env python3

"""VM 侧 WireGuard Agent。

预装在 VM image 中，作为 systemd service 运行。
网络名称从配置文件 (wg_network.json) 的 networkName 字段自动确定，无需传参。

工作流程:
    1. 加载网络配置 → 读取 networkName
    2. 生成/加载密钥对 → 发布公钥到 Host inventory
    3. 轮询 Host inventory，等待 Orchestrator 回填配置
    4. IP 齐备后生成 wg.conf → 激活 WireGuard 接口
    5. 继续轮询，Peer 变更时增量更新

用法:
    python3 wg_agent.py
    python3 wg_agent.py --publish-only
    python3 wg_agent.py --once
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

from domain.wireguard.wg_data import WgNetworkConfig
from domain.wireguard.wg_config_file import WgConfigFile
from domain.wireguard.wg_config_builder import WgConfigBuilder
from domain.wireguard.wg_key_manager import WgKeyManager

# ── 默认路径 ──────────────────────────────────────────────────────────────

DEFAULT_AGENT_CONFIG = "/opt/unitao/wg_agent.conf"
DEFAULT_INVENTORY_TOOL = "/opt/unitao-server-config/inventory_tool.py"
DEFAULT_WG_DIR = "/etc/wireguard"
DEFAULT_POLL_INTERVAL = 30


class WgAgent:
    """WireGuard Mesh Agent。

    读取 install.py 生成的 agent config (wg_agent.conf)，从中获取:
        - networkConfigPath → WgNetworkConfig JSON
        - inventoryTool     → inventory_tool.py 路径
        - wgDir             → WireGuard 数据目录

    网络名称由 WgNetworkConfig 的 networkName 字段确定。
    """

    def __init__(self, agent_config_path: str = DEFAULT_AGENT_CONFIG):
        self._agent_config_path = agent_config_path

        # 从 agent config 加载，在 load_agent_config() 中设置
        self._network_config_path = None
        self._inventory_tool = DEFAULT_INVENTORY_TOOL
        self._wg_dir = DEFAULT_WG_DIR

        # 以下在 load_network_config() 中初始化
        self._network = None
        self._key_manager = None
        self._wg_conf_path = None

        # 运行时状态
        self._last_peer_ids = None  # 用于检测 peer 变更

    # ── 步骤 0: 加载 agent 配置 ──────────────────────────────────────────

    def load_agent_config(self):
        """加载 install.py 生成的 agent 配置文件。

        从中读取各路径，不包含 WireGuard 网络配置本身。
        """
        if not os.path.isfile(self._agent_config_path):
            raise FileNotFoundError(
                f"Agent config not found: {self._agent_config_path}\n"
                f"  Run install.py --network-config <WgNetworkConfig.json> during image build."
            )

        with open(self._agent_config_path, "r") as f:
            agent_conf = json.load(f)

        self._network_config_path = agent_conf.get("networkConfigPath", None)
        if not self._network_config_path:
            raise KeyError(
                f"Missing [networkConfigPath] in {self._agent_config_path}"
            )
        self._inventory_tool = agent_conf.get("inventoryTool", self._inventory_tool)
        self._wg_dir = agent_conf.get("wgDir", self._wg_dir)

        print(f"  Agent config: {self._agent_config_path}")
        print(f"  Network config: {self._network_config_path}")

    # ── 步骤 1: 加载网络配置 ─────────────────────────────────────────────

    def load_network_config(self) -> WgNetworkConfig:
        """加载 WgNetworkConfig，networkName 即 WG 接口名。

        同时初始化 key_manager 和 wg_conf_path。
        """
        if not os.path.isfile(self._network_config_path):
            raise FileNotFoundError(
                f"Network config not found: {self._network_config_path}"
            )
        cfg = WgNetworkConfig.from_file(self._network_config_path)
        self._network = cfg.network_name
        self._key_manager = WgKeyManager(self._network, self._wg_dir)
        self._wg_conf_path = os.path.join(self._wg_dir, f"{self._network}.conf")
        return cfg

    # ── 步骤 2: 密钥 ─────────────────────────────────────────────────────

    def ensure_keypair(self) -> tuple:
        """生成或加载密钥对。"""
        return self._key_manager.generate_keypair(force=False)

    # ── 步骤 3: inventory 通信 (通过 inventory_tool.py) ───────────────────

    def _run_inventory_tool(self, *args) -> tuple:
        """调用 inventory_tool.py，返回 (success, stdout)。

        当 shareInventoryData=True 时，inventory_tool.py 由 cloud-init 部署。
        """
        cmd = [sys.executable, self._inventory_tool] + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0, result.stdout
        except FileNotFoundError:
            print(f"  [WARN] inventory_tool not found: {self._inventory_tool}",
                  file=sys.stderr)
            return False, ""
        except subprocess.TimeoutExpired:
            print(f"  [WARN] inventory_tool timed out", file=sys.stderr)
            return False, ""

    def _inventory_post(self, data: dict) -> bool:
        """通过 inventory_tool.py POST JSON 数据。"""
        # 写入临时文件
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="wg_agent_", delete=False
            )
            json.dump(data, tmp)
            tmp.close()

            ok, _ = self._run_inventory_tool("--data", tmp.name)
            return ok
        finally:
            if tmp and os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def _inventory_get(self, filename: str) -> dict | None:
        """通过 inventory_tool.py GET 指定文件。"""
        ok, stdout = self._run_inventory_tool("--get", filename)
        if not ok:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None

    # ── 步骤 4: 发布公钥 ─────────────────────────────────────────────────

    def publish_public_key(self, public_key: str, network_cfg: WgNetworkConfig) -> bool:
        """发布 wireguard_config.json 到 Host inventory。

        包含 public_keys + network 参数 (peer_id, listen_port, dns_servers, peers={})。
        """
        wg_config = WgConfigFile.create_initial(
            public_key=public_key,
            listen_port=network_cfg.listen_port,
            dns_servers=network_cfg.dns_servers,
        )
        payload = wg_config.to_dict()

        print(f"  Publishing public key via {self._inventory_tool} ...")
        ok = self._inventory_post(payload)
        if ok:
            print(f"  Public key published.")
        else:
            print(f"  [WARN] Failed to publish.", file=sys.stderr)
        return ok

    # ── 步骤 5: 轮询配置 ─────────────────────────────────────────────────

    def fetch_wireguard_config(self) -> WgConfigFile | None:
        """从 Host inventory 拉取 wireguard_config.json。"""
        data = self._inventory_get("wireguard_config.json")
        if data is None:
            return None
        try:
            return WgConfigFile(data)
        except ValueError as e:
            print(f"  [WARN] Invalid wireguard_config.json: {e}", file=sys.stderr)
            return None

    def _has_changed(self, wg_config: WgConfigFile) -> bool:
        """通过比较 peer 公钥集合检测配置变更。"""
        current = frozenset(p.get("public_key", "") for p in wg_config.peers)
        if self._last_peer_ids is None:
            return True
        return current != self._last_peer_ids

    # ── 步骤 6: 应用配置 ─────────────────────────────────────────────────

    def apply_config(self, wg_config: WgConfigFile, private_key: str) -> bool:
        """生成 wg.conf 并激活 WireGuard 接口。"""
        network_cfg = self.load_network_config()

        conf_text = WgConfigBuilder.build_vm_config(
            network=network_cfg,
            self_ip=wg_config.assigned_id,
            private_key=private_key,
            peers=wg_config.peers,
        )

        os.makedirs(os.path.dirname(self._wg_conf_path), exist_ok=True)
        with open(self._wg_conf_path, "w") as f:
            f.write(conf_text + "\n")

        iface = self._network
        if self._key_manager.interface_exists(iface):
            print(f"  Updating existing interface [{iface}] ...")
            return self._sync_conf()
        else:
            print(f"  Bringing up interface [{iface}] ...")
            return self._wg_quick_up()

    def _wg_quick_up(self) -> bool:
        try:
            result = subprocess.run(
                ["wg-quick", "up", self._wg_conf_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"  [ERROR] wg-quick up failed: {result.stderr.strip()}", file=sys.stderr)
                return False
            print(f"  Interface [{self._network}] is up.")
            return True
        except FileNotFoundError:
            print(f"  [ERROR] wg-quick not found.", file=sys.stderr)
            return False

    def _sync_conf(self) -> bool:
        try:
            strip_result = subprocess.run(
                ["wg-quick", "strip", self._network],
                capture_output=True, text=True,
            )
            if strip_result.returncode != 0:
                print(f"  [WARN] wg-quick strip failed, falling back to restart.",
                      file=sys.stderr)
                return self._restart_interface()

            sync_result = subprocess.run(
                ["wg", "syncconf", self._network],
                input=strip_result.stdout,
                capture_output=True, text=True,
            )
            if sync_result.returncode != 0:
                print(f"  [ERROR] wg syncconf failed: {sync_result.stderr.strip()}", file=sys.stderr)
                return False
            print(f"  Interface [{self._network}] updated (syncconf).")
            return True
        except FileNotFoundError:
            print(f"  [ERROR] wg command not found.", file=sys.stderr)
            return False

    def _restart_interface(self) -> bool:
        try:
            subprocess.run(
                ["wg-quick", "down", self._network],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            pass
        return self._wg_quick_up()

    # ── 主循环 ────────────────────────────────────────────────────────────

    def run(
        self,
        publish_only: bool = False,
        once: bool = False,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
    ):
        print(f"=== WireGuard Agent ===\n")

        # 0. 加载 agent 配置 (install.py 生成的路径配置)
        print("[0/4] Loading agent config ...")
        self.load_agent_config()
        print(f"  Inventory tool: {self._inventory_tool}")

        # 1. 加载网络配置 (从中读取 networkName)
        print("\n[1/4] Loading network config ...")
        network_cfg = self.load_network_config()
        print(f"  Network: {self._network}")
        print(f"  Subnet:  {network_cfg.subnet}")

        # 2. 生成/加载密钥对
        print("\n[2/4] Ensuring keypair ...")
        private_key, public_key = self.ensure_keypair()
        print(f"  Public key: {public_key[:16]}...")

        # 3. 发布公钥 + 网络参数
        print("\n[3/4] Publishing public key ...")
        if not self.publish_public_key(public_key, network_cfg):
            print("  [ERROR] Failed to publish public key. Exiting.", file=sys.stderr)
            sys.exit(1)

        if publish_only:
            print("\nPublish-only mode. Done.")
            return

        # 5. 轮询循环
        print(f"\nEntering poll loop (interval={poll_interval}s) ...")
        applied = False

        while True:
            print(f"\n[{time.strftime('%H:%M:%S')}] Checking for config ...")
            wg_config = self.fetch_wireguard_config()

            if wg_config is None:
                print(f"  Config not available yet. Retrying in {poll_interval}s ...")
            elif not wg_config.has_config:
                print(f"  Public key published, waiting for Orchestrator to assign IP ...")
            elif not self._has_changed(wg_config) and applied:
                print(f"  Config unchanged.")
            elif not self._peers_have_all_ips(wg_config):
                print(f"  Waiting for all peer IPs to be assigned ...")
            else:
                print(f"  Config ready! Applying ...")
                if self.apply_config(wg_config, private_key):
                    self._last_peer_ids = frozenset(
                        p.get("public_key", "") for p in wg_config.peers
                    )
                    applied = True
                    if once:
                        print("Once mode: config applied. Done.")
                        return
                else:
                    print(f"  [WARN] Failed to apply config, will retry.", file=sys.stderr)

            if once:
                if not applied:
                    print("Once mode: config not yet available. Done.")
                return

            time.sleep(poll_interval)

    @staticmethod
    def _peers_have_all_ips(wg_config: WgConfigFile) -> bool:
        """检查所有 peers 是否已被分配 assigned_id。"""
        for peer in wg_config.peers:
            if not peer.get("assigned_id"):
                return False
        return True


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="WireGuard Mesh Agent — reads paths from wg_agent.conf"
    )
    parser.add_argument(
        "--agent-config", type=str, default=DEFAULT_AGENT_CONFIG,
        help=f"Path to agent config (default: {DEFAULT_AGENT_CONFIG})",
    )
    parser.add_argument(
        "--publish-only", action="store_true",
        help="Only publish public key, then exit",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Check and apply config once, then exit (for timer-triggered runs)",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})",
    )
    args = parser.parse_args()

    agent = WgAgent(agent_config_path=args.agent_config)
    agent.run(
        publish_only=args.publish_only,
        once=args.once,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    main()
