#!/usr/bin/env python3

"""WireGuard 领域基础数据模型。

纯数据层，只做校验和承载，不涉及系统调用、文件 I/O、文本生成。

模块内容:
    - IP 地址工具函数: parse_cidr, ip_to_int, int_to_ip, ip_in_subnet, validate_ipv4, validate_cidr
    - WgNetworkConfig:   网络配置 JSON 校验与承载
    - WgPeerData:        Peer 连接信息校验与承载
"""

import json
import re


# ══════════════════════════════════════════════════════════════════════════════
# IP 地址工具函数
# ══════════════════════════════════════════════════════════════════════════════

def validate_ipv4(ip: str):
    """校验 IPv4 地址格式，不合法抛 ValueError。

    Args:
        ip: IPv4 地址字符串，如 "10.200.0.1"

    Raises:
        ValueError: 格式不合法
    """
    if not isinstance(ip, str) or not ip.strip():
        raise ValueError(f"Invalid IPv4 address: [{ip}], must be a non-empty string")

    parts = ip.split(".")
    if len(parts) != 4:
        raise ValueError(f"Invalid IPv4 address: [{ip}], expected 4 octets")

    for i, part in enumerate(parts):
        if not part.isdigit():
            raise ValueError(
                f"Invalid IPv4 address: [{ip}], octet [{part}] is not a digit"
            )
        num = int(part)
        if num < 0 or num > 255:
            raise ValueError(
                f"Invalid IPv4 address: [{ip}], octet [{part}] out of range 0-255"
            )


def validate_cidr(cidr: str):
    """校验 CIDR 格式，不合法抛 ValueError。

    Args:
        cidr: CIDR 字符串，如 "10.200.0.0/24"

    Raises:
        ValueError: 格式不合法
    """
    if not isinstance(cidr, str) or not cidr.strip():
        raise ValueError(f"Invalid CIDR: [{cidr}], must be a non-empty string")

    if "/" not in cidr:
        raise ValueError(f"Invalid CIDR: [{cidr}], missing '/' separator")

    parts = cidr.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid CIDR: [{cidr}], expected format ip/prefix")

    ip_part, prefix_part = parts
    validate_ipv4(ip_part)

    if not prefix_part.isdigit():
        raise ValueError(
            f"Invalid CIDR: [{cidr}], prefix [{prefix_part}] is not a digit"
        )
    prefix = int(prefix_part)
    if prefix < 0 or prefix > 32:
        raise ValueError(
            f"Invalid CIDR: [{cidr}], prefix [{prefix}] out of range 0-32"
        )


def parse_cidr(cidr: str) -> tuple:
    """解析 CIDR 为 (IP地址, 前缀长度)。

    Args:
        cidr: CIDR 字符串，如 "10.200.0.10/24"

    Returns:
        (ip_address, prefix_length) 如 ("10.200.0.10", 24)
    """
    validate_cidr(cidr)
    ip, prefix = cidr.split("/")
    return ip, int(prefix)


def ip_to_int(ip: str) -> int:
    """将 IPv4 地址转换为 32 位无符号整数。

    Args:
        ip: IPv4 地址，如 "10.200.0.10"

    Returns:
        整数表示，如 0x0AC8000A (181207050)
    """
    validate_ipv4(ip)
    octets = ip.split(".")
    return (int(octets[0]) << 24) | (int(octets[1]) << 16) | (int(octets[2]) << 8) | int(octets[3])


def int_to_ip(num: int) -> str:
    """将 32 位整数转换为 IPv4 点分十进制。

    Args:
        num: 32 位无符号整数

    Returns:
        IPv4 地址字符串，如 "10.200.0.10"

    Raises:
        ValueError: num 超出 0 ~ 0xFFFFFFFF 范围
    """
    if not isinstance(num, int) or num < 0 or num > 0xFFFFFFFF:
        raise ValueError(
            f"Invalid IP integer: [{num}], must be 0-{0xFFFFFFFF} (32-bit unsigned)"
        )
    return f"{(num >> 24) & 0xFF}.{(num >> 16) & 0xFF}.{(num >> 8) & 0xFF}.{num & 0xFF}"


def ip_in_subnet(ip: str, subnet_cidr: str) -> bool:
    """判断 IP 地址是否在给定子网 CIDR 范围内。

    Args:
        ip: IPv4 地址
        subnet_cidr: CIDR 子网，如 "10.200.0.0/24"

    Returns:
        True 如果 IP 在子网内
    """
    validate_ipv4(ip)
    subnet_ip, prefix = parse_cidr(subnet_cidr)

    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    ip_int = ip_to_int(ip)
    subnet_int = ip_to_int(subnet_ip)

    return (ip_int & mask) == (subnet_int & mask)


# ══════════════════════════════════════════════════════════════════════════════
# WgNetworkConfig — 网络配置模型
# ══════════════════════════════════════════════════════════════════════════════

class WgNetworkConfig:
    """WireGuard 网络配置的校验和承载。

    wireguard_network.json 和 wireguard_network_inv.json 共享同一 schema。

    JSON Schema:
        {
          "data": {
            "public_keys": {
              "primary": "base64..."
            },
            "network": {
              "assigned_ip": "10.200.0.1/32",
              "listen_port": 51820,
              "dns_servers": ["8.8.8.8"],
              "peers": [
                {
                  "ip": "10.200.0.2/32",
                  "endpoint": "192.168.1.105:51820",
                  "publicKey": "base64..."
                }
              ]
            }
          }
        }
    """

    class Key:
        DATA                       = "data"
        DATA_PUBLIC_KEYS           = "public_keys"
        DATA_PUBLIC_KEYS_PRIMARY   = "primary"
        DATA_NETWORK               = "network"
        DATA_NETWORK_ASSIGNED_IP   = "assigned_ip"
        DATA_NETWORK_LISTEN_PORT   = "listen_port"
        DATA_NETWORK_DNS_SERVERS   = "dns_servers"
        DATA_NETWORK_PEERS         = "peers"
        DATA_NETWORK_POST_UP      = "post_up"
        DATA_NETWORK_POST_DOWN    = "post_down"
        DATA_NETWORK_SWITCH       = "switch"

    class PeerKey:
        PUBLIC_KEY = "publicKey"
        IP         = "ip"
        ENDPOINT   = "endpoint"

    DEFAULT_LISTEN_PORT = 51820

    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise ValueError(
                f"WgNetworkConfig: data must be a dict, got {type(data).__name__}"
            )
        self._data = data
        self.validate()

    # ── data 子结构访问 ─────────────────────────────────────────────────

    def _ensure_data(self):
        if self.Key.DATA not in self._data:
            self._data[self.Key.DATA] = {}
        return self._data[self.Key.DATA]

    def _ensure_public_keys(self):
        d = self._ensure_data()
        if self.Key.DATA_PUBLIC_KEYS not in d:
            d[self.Key.DATA_PUBLIC_KEYS] = {}
        return d[self.Key.DATA_PUBLIC_KEYS]

    def _ensure_network(self):
        d = self._ensure_data()
        if self.Key.DATA_NETWORK not in d:
            d[self.Key.DATA_NETWORK] = {}
        return d[self.Key.DATA_NETWORK]

    # ── 校验 ────────────────────────────────────────────────────────────

    def validate(self):
        K = self.Key
        data = self._data

        # --- data: 必填, dict ---
        if K.DATA not in data:
            raise ValueError(f"WgNetworkConfig: missing [{K.DATA}]")
        d = data[K.DATA]
        if not isinstance(d, dict):
            raise ValueError(
                f"WgNetworkConfig: [{K.DATA}] must be a dict"
            )

        # --- data.public_keys: 必填, dict ---
        if K.DATA_PUBLIC_KEYS not in d:
            raise ValueError(
                f"WgNetworkConfig: missing [{K.DATA}.{K.DATA_PUBLIC_KEYS}]"
            )
        pkeys = d[K.DATA_PUBLIC_KEYS]
        if not isinstance(pkeys, dict):
            raise ValueError(
                f"WgNetworkConfig: [{K.DATA}.{K.DATA_PUBLIC_KEYS}] must be a dict"
            )

        # --- data.public_keys.primary: 可选, str (空字符串表示未生成) ---
        pk = pkeys.get(K.DATA_PUBLIC_KEYS_PRIMARY, None)
        if pk is not None and pk != "" and (not isinstance(pk, str) or not pk.strip()):
            raise ValueError(
                f"WgNetworkConfig: [{K.DATA}.{K.DATA_PUBLIC_KEYS}.{K.DATA_PUBLIC_KEYS_PRIMARY}] must be a string"
            )

        # --- data.network: 必填, dict ---
        if K.DATA_NETWORK not in d:
            raise ValueError(
                f"WgNetworkConfig: missing [{K.DATA}.{K.DATA_NETWORK}]"
            )
        net = d[K.DATA_NETWORK]
        if not isinstance(net, dict):
            raise ValueError(
                f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}] must be a dict"
            )

        # --- data.network.assigned_ip: 可选, 合法 CIDR (空字符串/不存在表示未分配) ---
        aip = net.get(K.DATA_NETWORK_ASSIGNED_IP, None)
        if aip is not None and aip != "":
            if not isinstance(aip, str):
                raise ValueError(
                    f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_ASSIGNED_IP}] must be a string"
                )
            validate_cidr(aip)

        # --- data.network.listen_port: 可选, int 1-65535 ---
        lp = net.get(K.DATA_NETWORK_LISTEN_PORT, None)
        if lp is not None and (not isinstance(lp, int) or lp < 1 or lp > 65535):
            raise ValueError(
                f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_LISTEN_PORT}] must be int 1-65535"
            )

        # --- data.network.dns_servers: 可选, list[str] ---
        dns = net.get(K.DATA_NETWORK_DNS_SERVERS, None)
        if dns is not None:
            if not isinstance(dns, list):
                raise ValueError(
                    f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_DNS_SERVERS}] must be a list"
                )
            for i, server in enumerate(dns):
                if not isinstance(server, str):
                    raise ValueError(
                        f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_DNS_SERVERS}][{i}] must be a string"
                    )
                validate_ipv4(server)

        # --- data.network.peers: 可选, list[dict] ---
        peers = net.get(K.DATA_NETWORK_PEERS, None)
        if peers is not None:
            if not isinstance(peers, list):
                raise ValueError(
                    f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_PEERS}] must be a list"
                )

        # --- data.network.post_up: 可选, list[str] 或 str ---
        post_up = net.get(K.DATA_NETWORK_POST_UP, None)
        if post_up is not None:
            if not isinstance(post_up, (list, str)):
                raise ValueError(
                    f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_POST_UP}] must be a string or list of strings"
                )
            if isinstance(post_up, list):
                for i, cmd in enumerate(post_up):
                    if not isinstance(cmd, str):
                        raise ValueError(
                            f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_POST_UP}][{i}] must be a string"
                        )

        # --- data.network.post_down: 可选, list[str] 或 str ---
        post_down = net.get(K.DATA_NETWORK_POST_DOWN, None)
        if post_down is not None:
            if not isinstance(post_down, (list, str)):
                raise ValueError(
                    f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_POST_DOWN}] must be a string or list of strings"
                )
            if isinstance(post_down, list):
                for i, cmd in enumerate(post_down):
                    if not isinstance(cmd, str):
                        raise ValueError(
                            f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_POST_DOWN}][{i}] must be a string"
                        )

        # --- data.network.switch: 可选, "on" | "off" ---
        sw = net.get(K.DATA_NETWORK_SWITCH, None)
        if sw is not None and sw not in ("on", "off"):
            raise ValueError(
                f"WgNetworkConfig: [{K.DATA}.{K.DATA_NETWORK}.{K.DATA_NETWORK_SWITCH}] must be 'on' or 'off', got [{sw}]"
            )

    # ── 属性访问器 ──────────────────────────────────────────────────────

    @property
    def primary_public_key(self) -> str:
        """Agent 生成的 primary 公钥，未生成时返回空字符串。"""
        pk = self._ensure_public_keys()
        return pk.get(self.Key.DATA_PUBLIC_KEYS_PRIMARY, "")

    @property
    def assigned_ip(self) -> str | None:
        """VM 在 VPN 中的 IP (CIDR)，Orchestrator 分配。未分配时返回 None。"""
        net = self._ensure_network()
        aip = net.get(self.Key.DATA_NETWORK_ASSIGNED_IP, None)
        return aip if aip else None

    @property
    def listen_port(self) -> int:
        net = self._ensure_network()
        return net.get(self.Key.DATA_NETWORK_LISTEN_PORT, self.DEFAULT_LISTEN_PORT)

    @property
    def dns_servers(self) -> list:
        net = self._ensure_network()
        return net.get(self.Key.DATA_NETWORK_DNS_SERVERS, [])

    @property
    def peers(self) -> list:
        """Peer 列表 [{publicKey, ip, endpoint}, ...]."""
        net = self._ensure_network()
        return list(net.get(self.Key.DATA_NETWORK_PEERS, []))

    @property
    def post_up(self) -> list | None:
        """PostUp 命令列表。"""
        net = self._ensure_network()
        val = net.get(self.Key.DATA_NETWORK_POST_UP, None)
        if val is None:
            return None
        return val if isinstance(val, list) else [val]

    @property
    def post_down(self) -> list | None:
        """PostDown 命令列表。"""
        net = self._ensure_network()
        val = net.get(self.Key.DATA_NETWORK_POST_DOWN, None)
        if val is None:
            return None
        return val if isinstance(val, list) else [val]

    @property
    def switch(self) -> str:
        """开关状态，Orchestrator 控制。默认 "on" 保持向后兼容。"""
        net = self._ensure_network()
        return net.get(self.Key.DATA_NETWORK_SWITCH, "on")

    @property
    def is_switched_off(self) -> bool:
        return self.switch == "off"

    @property
    def has_network_config(self) -> bool:
        """assigned_ip 非空且 switch 非 off 即可生成 wg.conf。"""
        aip = self.assigned_ip
        return aip is not None and aip != ""

    # ── 序列化 ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return dict(self._data)

    def save_to_file(self, path: str):
        with open(path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    @staticmethod
    def from_file(path: str) -> "WgNetworkConfig":
        with open(path, "r") as f:
            data = json.load(f)
        return WgNetworkConfig(data)

    # ── 工厂方法 ────────────────────────────────────────────────────────

    @staticmethod
    def create_initial(public_key: str) -> "WgNetworkConfig":
        """创建初始 wireguard_network.json（Agent 首次运行时调用）。

        Args:
            public_key: Agent 生成的 primary 公钥
        """
        if not isinstance(public_key, str) or not public_key.strip():
            raise ValueError("create_initial: public_key must be a non-empty string")
        data = {
            WgNetworkConfig.Key.DATA: {
                WgNetworkConfig.Key.DATA_PUBLIC_KEYS: {
                    WgNetworkConfig.Key.DATA_PUBLIC_KEYS_PRIMARY: public_key,
                },
                WgNetworkConfig.Key.DATA_NETWORK: {
                    WgNetworkConfig.Key.DATA_NETWORK_PEERS: [],
                },
            },
        }
        return WgNetworkConfig(data)

    # ── 合并 ────────────────────────────────────────────────────────────

    def merge_network_from(self, other: "WgNetworkConfig", api_timestamp: str) -> bool:
        """从 inv 配置合并 data.network，保留 data.public_keys。

        比对由调用方通过 API timestamp 判断，此处总是执行合并。

        Args:
            other:         从 inventory 下载的 WgNetworkConfig
            api_timestamp: Host REST API 返回的文件修改时间

        Returns:
            True (总是合并)
        """
        net = other._ensure_network()
        local_net = self._ensure_network()

        # 覆盖 network 字段
        for key in (
            self.Key.DATA_NETWORK_ASSIGNED_IP,
            self.Key.DATA_NETWORK_LISTEN_PORT,
            self.Key.DATA_NETWORK_DNS_SERVERS,
            self.Key.DATA_NETWORK_PEERS,
            self.Key.DATA_NETWORK_POST_UP,
            self.Key.DATA_NETWORK_POST_DOWN,
            self.Key.DATA_NETWORK_SWITCH,
        ):
            if key in net:
                local_net[key] = net[key]

        return True


# ══════════════════════════════════════════════════════════════════════════════
# WgPeerData — Peer 连接信息
# ══════════════════════════════════════════════════════════════════════════════

class WgPeerData:
    """WireGuard Peer 配置数据校验和承载。

    JSON 格式 (外部应用 → VM inventory):
        {
          "publicKey": "qH3q5X...base64...",
          "endpoint": "192.168.122.11:51820",
          "assigned_id": "10.200.0.11/32",
          "allowedIPs": ["10.200.0.11/32"]
        }

    | 字段 | 说明 |
    |------|------|
    | publicKey | Peer 的 WireGuard 公钥 (44 字符 base64) |
    | endpoint | Peer 的可达地址 ip:port |
    | assigned_id | Peer 在 VPN 子网中的 IP (CIDR /32) |
    | allowedIPs | 通过该 Peer 可到达的 IP 范围 |
    """

    BASE64_RE = re.compile(r'^[A-Za-z0-9+/=]+$')

    class Key:
        PUBLIC_KEY  = "public_key"
        ENDPOINT    = "endpoint"
        ASSIGNED_ID = "assigned_id"
        ALLOWED_IPS = "allowed_ips"

    def __init__(self, data: dict):
        """从 JSON 字典构建并校验。

        Args:
            data: Peer 配置字典

        Raises:
            ValueError: 校验失败
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"WgPeerData: data must be a dict, got {type(data).__name__}"
            )
        self._data = data
        self.validate()

    def validate(self):
        """逐字段校验 Peer 数据。

        校验规则:
            - publicKey:  非空 str, 长度 44, base64 字符集
            - endpoint:   host:port 格式, port 1-65535
            - assigned_id: 合法 CIDR
            - allowedIPs: list[str], 每项合法 CIDR, 非空

        Raises:
            ValueError: 校验失败
        """
        K = self.Key
        data = self._data

        # --- publicKey: 非空 str, 长度 44, base64 字符集 ---
        if K.PUBLIC_KEY not in data:
            raise ValueError(f"WgPeerData: missing required key [{K.PUBLIC_KEY}]")
        pubkey = data[K.PUBLIC_KEY]
        if not isinstance(pubkey, str) or not pubkey.strip():
            raise ValueError(
                f"WgPeerData: [{K.PUBLIC_KEY}] must be a non-empty string"
            )
        if len(pubkey) != 44:
            raise ValueError(
                f"WgPeerData: [{K.PUBLIC_KEY}] length must be 44, got {len(pubkey)}"
            )
        if not self.BASE64_RE.match(pubkey):
            raise ValueError(
                f"WgPeerData: [{K.PUBLIC_KEY}] must be base64 (A-Za-z0-9+/=)"
            )

        # --- endpoint: host:port 格式, port 1-65535 ---
        if K.ENDPOINT in data and data[K.ENDPOINT] is not None:
            ep = data[K.ENDPOINT]
            if not isinstance(ep, str):
                raise ValueError(
                    f"WgPeerData: [{K.ENDPOINT}] must be a string"
                )
            if ":" not in ep:
                raise ValueError(
                    f"WgPeerData: [{K.ENDPOINT}] must be host:port format, got [{ep}]"
                )
            # 从右侧分割：IPv6 地址包含多个冒号
            host_part, port_part = ep.rsplit(":", 1)
            # 基本 host 校验：不能为空
            if not host_part.strip():
                raise ValueError(
                    f"WgPeerData: [{K.ENDPOINT}] host part is empty in [{ep}]"
                )
            if not port_part.isdigit():
                raise ValueError(
                    f"WgPeerData: [{K.ENDPOINT}] port must be numeric in [{ep}]"
                )
            port = int(port_part)
            if port < 1 or port > 65535:
                raise ValueError(
                    f"WgPeerData: [{K.ENDPOINT}] port {port} out of range 1-65535"
                )

        # --- assigned_id: 合法 CIDR ---
        if K.ASSIGNED_ID in data and data[K.ASSIGNED_ID] is not None:
            aip = data[K.ASSIGNED_ID]
            if not isinstance(aip, str):
                raise ValueError(
                    f"WgPeerData: [{K.ASSIGNED_ID}] must be a string"
                )
            validate_cidr(aip)

        # --- allowedIPs: list[str], 每项合法 CIDR, 非空 ---
        if K.ALLOWED_IPS in data and data[K.ALLOWED_IPS] is not None:
            allowed = data[K.ALLOWED_IPS]
            if not isinstance(allowed, list):
                raise ValueError(
                    f"WgPeerData: [{K.ALLOWED_IPS}] must be a list, got {type(allowed).__name__}"
                )
            if len(allowed) == 0:
                raise ValueError(
                    f"WgPeerData: [{K.ALLOWED_IPS}] must not be empty"
                )
            for i, cidr in enumerate(allowed):
                if not isinstance(cidr, str):
                    raise ValueError(
                        f"WgPeerData: [{K.ALLOWED_IPS}][{i}] must be a string"
                    )
                validate_cidr(cidr)

    # ── 属性访问器 ──────────────────────────────────────────────────────────

    @property
    def public_key(self) -> str:
        return self._data[self.Key.PUBLIC_KEY]

    @property
    def endpoint(self) -> str:
        """返回 endpoint 字符串，如 "192.168.122.11:51820"，未设时返回 None。"""
        return self._data.get(self.Key.ENDPOINT, None)

    @property
    def assigned_id(self) -> str:
        """返回 assigned_id，如 "10.200.0.11/32"，未设时返回 None。"""
        return self._data.get(self.Key.ASSIGNED_ID, None)

    @property
    def assigned_ip_address(self) -> str:
        """返回 assigned_id 中的纯 IP 地址部分，如 "10.200.0.11"，未设时返回 None。"""
        cidr = self.assigned_id
        if cidr is None:
            return None
        ip, _ = parse_cidr(cidr)
        return ip

    @property
    def allowed_ips(self) -> list:
        return self._data.get(self.Key.ALLOWED_IPS, [])

    def to_dict(self) -> dict:
        """导出为字典。"""
        return dict(self._data)
