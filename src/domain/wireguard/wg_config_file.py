#!/usr/bin/env python3

"""wireguard_config.json 的数据结构。

VM 发布公钥和网络参数，Orchestrator 回填 peer 信息和 assigned_id。

JSON 格式:
    {
      "public_keys": {
        "primary": "<public_key>"
      },
      "network": {
        "assigned_id": "10.200.0.1",
        "listen_port": 51820,
        "dns_servers": ["8.8.8.8"],
        "peers": [
          {
            "public_key": "...",
            "endpoint": "192.168.122.11:51820",
            "assigned_id": "10.200.0.11",
            "allowed_ips": ["10.200.0.11/32"]
          }
        ]
      }
    }

交互序列:
    1. VM 发布: public_keys + network (listen_port, dns_servers, peers=[])
    2. Orchestrator 回填: network.assigned_id + network.peers (含各 peer 的 public_key, endpoint, assigned_id, allowed_ips)
    3. VM Agent 检测 assigned_id 非空 → 生成 wg.conf → 激活

依赖: wg_data.py (WgPeerData)
"""

import json

from domain.wireguard.wg_data import validate_cidr


class WgConfigFile:
    """wireguard_config.json 的数据结构。

    VM 发布初始数据 (public_keys + network 参数)，Orchestrator 回填 assigned_id 和 peers。
    """

    INVENTORY_NAME = "wireguard_config"  # inventory 存储的文件名 (不含 .json)

    class Key:
        PUBLIC_KEYS = "public_keys"
        NETWORK     = "network"

    class PublicKeysKey:
        PRIMARY = "primary"

    class NetworkKey:
        ASSIGNED_ID  = "assigned_id"
        LISTEN_PORT  = "listen_port"
        DNS_SERVERS  = "dns_servers"
        PEERS        = "peers"

    def __init__(self, data: dict):
        """从字典构建并校验。

        Args:
            data: wireguard_config.json 的完整内容

        Raises:
            ValueError: 校验失败
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"WgConfigFile: input must be a dict, got {type(data).__name__}"
            )
        self._data = data
        self._peers = {}  # dict: peer_id → WgPeerData
        self.validate()

    def validate(self):
        """校验结构。

        校验规则:
            - public_keys.primary: 必填, 非空 str
            - network.assigned_id: 若存在, 非空 str
            - network.listen_port: 若存在, int 1-65535
            - network.dns_servers: 若存在, list[str]
            - network.peers: list, 每项含 public_key

        Raises:
            ValueError: 校验失败
        """
        K = self.Key
        NK = self.NetworkKey
        PK = self.PublicKeysKey
        data = self._data

        # --- public_keys.primary: 必填, 非空 str ---
        if K.PUBLIC_KEYS not in data:
            raise ValueError(f"WgConfigFile: missing [{K.PUBLIC_KEYS}]")
        pkeys = data[K.PUBLIC_KEYS]
        if not isinstance(pkeys, dict):
            raise ValueError(
                f"WgConfigFile: [{K.PUBLIC_KEYS}] must be a dict, got {type(pkeys).__name__}"
            )
        if PK.PRIMARY not in pkeys:
            raise ValueError(
                f"WgConfigFile: missing [{K.PUBLIC_KEYS}.{PK.PRIMARY}]"
            )
        pubkey = pkeys[PK.PRIMARY]
        if not isinstance(pubkey, str) or not pubkey.strip():
            raise ValueError(
                f"WgConfigFile: [{K.PUBLIC_KEYS}.{PK.PRIMARY}] must be a non-empty string"
            )

        # --- network: 必须存在 ---
        if K.NETWORK not in data:
            raise ValueError(f"WgConfigFile: missing [{K.NETWORK}]")
        net = data[K.NETWORK]
        if not isinstance(net, dict):
            raise ValueError(
                f"WgConfigFile: [{K.NETWORK}] must be a dict, got {type(net).__name__}"
            )

        # --- network.assigned_id: 若存在, 非空 str (IP 或 CIDR) ---
        if NK.ASSIGNED_ID in net:
            aid = net[NK.ASSIGNED_ID]
            if aid is not None and (not isinstance(aid, str) or not aid.strip()):
                raise ValueError(
                    f"WgConfigFile: [{K.NETWORK}.{NK.ASSIGNED_ID}] must be a non-empty string"
                )

        # --- network.listen_port: 若存在, int 1-65535 ---
        if NK.LISTEN_PORT in net:
            port = net[NK.LISTEN_PORT]
            if port is not None and (not isinstance(port, int) or port < 1 or port > 65535):
                raise ValueError(
                    f"WgConfigFile: [{K.NETWORK}.{NK.LISTEN_PORT}] must be int 1-65535"
                )

        # --- network.dns_servers: 若存在, list[str] ---
        if NK.DNS_SERVERS in net:
            dns = net[NK.DNS_SERVERS]
            if dns is not None and not isinstance(dns, list):
                raise ValueError(
                    f"WgConfigFile: [{K.NETWORK}.{NK.DNS_SERVERS}] must be a list"
                )

        # --- network.peers: list, 每项含 public_key ---
        if NK.PEERS in net:
            peers_raw = net[NK.PEERS]
            if peers_raw is not None:
                if not isinstance(peers_raw, list):
                    raise ValueError(
                        f"WgConfigFile: [{K.NETWORK}.{NK.PEERS}] must be a list, got {type(peers_raw).__name__}"
                    )
                for i, peer_dict in enumerate(peers_raw):
                    if not isinstance(peer_dict, dict):
                        raise ValueError(
                            f"WgConfigFile: [{K.NETWORK}.{NK.PEERS}][{i}] must be a dict"
                        )
                    if "public_key" not in peer_dict:
                        raise ValueError(
                            f"WgConfigFile: [{K.NETWORK}.{NK.PEERS}][{i}] missing [public_key]"
                        )

    # ── 属性访问器 ──────────────────────────────────────────────────────────

    @property
    def self_public_key(self) -> str:
        return self._data[self.Key.PUBLIC_KEYS][self.PublicKeysKey.PRIMARY]

    @property
    def assigned_id(self) -> str | None:
        """本 VM 被分配的 IP (CIDR)，Orchestrator 回填。未分配时返回 None。"""
        return self._data.get(self.Key.NETWORK, {}).get(self.NetworkKey.ASSIGNED_ID, None)

    @property
    def listen_port(self) -> int | None:
        """VM 的 WireGuard 监听端口。"""
        return self._data.get(self.Key.NETWORK, {}).get(self.NetworkKey.LISTEN_PORT, None)

    @property
    def dns_servers(self) -> list:
        """VM 宣告的 DNS 服务器列表。"""
        return self._data.get(self.Key.NETWORK, {}).get(self.NetworkKey.DNS_SERVERS, [])

    @property
    def peers(self) -> list:
        """Peer 列表 [{public_key, endpoint, assigned_id, allowed_ips}, ...]."""
        return list(self._data.get(self.Key.NETWORK, {}).get(self.NetworkKey.PEERS, []))

    @property
    def has_config(self) -> bool:
        """assigned_id 非空即可生成配置。"""
        return self.assigned_id is not None and self.assigned_id != ""

    # ── 序列化 ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """导出完整字典。"""
        return dict(self._data)

    # ── 工厂方法 ────────────────────────────────────────────────────────────

    @staticmethod
    def from_file(path: str) -> "WgConfigFile":
        """从 JSON 文件加载。"""
        with open(path, "r") as f:
            data = json.load(f)
        return WgConfigFile(data)

    @staticmethod
    def create_initial(
        public_key: str,
        listen_port: int = None,
        dns_servers: list = None,
    ) -> "WgConfigFile":
        """VM 首次发布: public_keys + network 参数, peers 为空。

        Args:
            public_key:  VM 的 WireGuard 公钥
            listen_port: 监听端口 (可选)
            dns_servers: DNS 服务器列表 (可选)
        """
        if not isinstance(public_key, str) or not public_key.strip():
            raise ValueError("WgConfigFile.create_initial: public_key must be a non-empty string")

        data = {
            WgConfigFile.Key.PUBLIC_KEYS: {
                WgConfigFile.PublicKeysKey.PRIMARY: public_key,
            },
            WgConfigFile.Key.NETWORK: {
                WgConfigFile.NetworkKey.PEERS: [],
            },
        }

        net = data[WgConfigFile.Key.NETWORK]
        if listen_port:
            net[WgConfigFile.NetworkKey.LISTEN_PORT] = listen_port
        if dns_servers:
            net[WgConfigFile.NetworkKey.DNS_SERVERS] = dns_servers

        return WgConfigFile(data)
