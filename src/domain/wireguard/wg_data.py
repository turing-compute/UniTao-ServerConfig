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
    """WireGuard Mesh 网络配置的校验和承载。

    描述一个 VM Mesh 网络的参数。配置文件由外部应用或管理员放置在 VM image 中
    或通过 cloud-init 注入，VM Agent 启动时读取。

    JSON Schema (在 VM 上的路径由 Agent 配置决定，例如 /opt/unitao/wg-mesh.json):
        {
          "networkName": "wg-mesh",             // 必填, string, 网络唯一标识
          "subnet": "10.200.0.0/24",            // 必填, CIDR, VPN 子网
          "listenPort": 51820,                  // 必填, int 1-65535, VM 监听端口
          "dnsServers": ["10.200.0.1"],         // 可选, [ip]
          "mtu": 1420,                          // 可选, int 1280-1500, 默认 1420
          "persistentKeepalive": 25,            // 可选, int 0-65535, 默认 25
          "routes": [                           // 可选, [route], 通过 VPN 的路由
            {"destination": "10.200.0.0/24", "description": "VPN 子网"}
          ],
          "postUp": "iptables -A FORWARD -i %i -j ACCEPT",    // 可选
          "postDown": "iptables -D FORWARD -i %i -j ACCEPT"   // 可选
        }
    """

    class Key:
        NETWORK_NAME         = "networkName"
        SUBNET               = "subnet"
        LISTEN_PORT          = "listenPort"
        DNS_SERVERS          = "dnsServers"
        MTU                  = "mtu"
        PERSISTENT_KEEPALIVE = "persistentKeepalive"
        ROUTES               = "routes"
        POST_UP              = "postUp"
        POST_DOWN            = "postDown"

    DEFAULT_MTU = 1420
    DEFAULT_KEEPALIVE = 25

    def __init__(self, data: dict):
        """从 JSON 字典构建并校验。

        Args:
            data: 网络配置字典

        Raises:
            ValueError: 校验失败
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"WgNetworkConfig: data must be a dict, got {type(data).__name__}"
            )
        self._data = data
        self.validate()

    def validate(self):
        """校验网络配置的合法性和完整性。

        校验规则:
            networkName:         必填, 非空 str
            subnet:              必填, 合法 CIDR, netmask 8-30
            listenPort:          必填, int, 1-65535
            dnsServers:          若存在: list[str], 每项合法 IPv4
            mtu:                 若存在: int, 1280-1500
            persistentKeepalive: 若存在: int, 0-65535
            routes:              若存在: list[dict], 每项 .destination 合法 CIDR
            postUp/postDown:     若存在: str

        Raises:
            ValueError: 校验失败
        """
        K = self.Key
        data = self._data

        # --- networkName: 必填, 非空 str ---
        if K.NETWORK_NAME not in data:
            raise ValueError(f"WgNetworkConfig: missing required key [{K.NETWORK_NAME}]")
        name = data[K.NETWORK_NAME]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"WgNetworkConfig: [{K.NETWORK_NAME}] must be a non-empty string, got [{name}]"
            )

        # --- subnet: 必填, 合法 CIDR, netmask 8-30 ---
        if K.SUBNET not in data:
            raise ValueError(f"WgNetworkConfig: missing required key [{K.SUBNET}]")
        subnet = data[K.SUBNET]
        validate_cidr(subnet)
        _, prefix = parse_cidr(subnet)
        if prefix < 8 or prefix > 30:
            raise ValueError(
                f"WgNetworkConfig: [{K.SUBNET}] netmask must be 8-30, got /{prefix} in [{subnet}]"
            )

        # --- listenPort: 必填, int, 1-65535 ---
        if K.LISTEN_PORT not in data:
            raise ValueError(f"WgNetworkConfig: missing required key [{K.LISTEN_PORT}]")
        port = data[K.LISTEN_PORT]
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError(
                f"WgNetworkConfig: [{K.LISTEN_PORT}] must be int 1-65535, got [{port}]"
            )

        # --- dnsServers: 可选, list[str], 每项合法 IPv4 ---
        if K.DNS_SERVERS in data:
            dns = data[K.DNS_SERVERS]
            if not isinstance(dns, list):
                raise ValueError(
                    f"WgNetworkConfig: [{K.DNS_SERVERS}] must be a list, got {type(dns).__name__}"
                )
            for i, server in enumerate(dns):
                if not isinstance(server, str):
                    raise ValueError(
                        f"WgNetworkConfig: [{K.DNS_SERVERS}][{i}] must be a string"
                    )
                validate_ipv4(server)

        # --- mtu: 可选, int, 1280-1500 ---
        if K.MTU in data:
            mtu = data[K.MTU]
            if not isinstance(mtu, int) or mtu < 1280 or mtu > 1500:
                raise ValueError(
                    f"WgNetworkConfig: [{K.MTU}] must be int 1280-1500, got [{mtu}]"
                )

        # --- persistentKeepalive: 可选, int, 0-65535 ---
        if K.PERSISTENT_KEEPALIVE in data:
            keepalive = data[K.PERSISTENT_KEEPALIVE]
            if not isinstance(keepalive, int) or keepalive < 0 or keepalive > 65535:
                raise ValueError(
                    f"WgNetworkConfig: [{K.PERSISTENT_KEEPALIVE}] must be int 0-65535, got [{keepalive}]"
                )

        # --- routes: 可选, list[dict], 每项 .destination 合法 CIDR ---
        if K.ROUTES in data:
            routes = data[K.ROUTES]
            if not isinstance(routes, list):
                raise ValueError(
                    f"WgNetworkConfig: [{K.ROUTES}] must be a list, got {type(routes).__name__}"
                )
            for i, route in enumerate(routes):
                if not isinstance(route, dict):
                    raise ValueError(
                        f"WgNetworkConfig: [{K.ROUTES}][{i}] must be a dict"
                    )
                if "destination" not in route:
                    raise ValueError(
                        f"WgNetworkConfig: [{K.ROUTES}][{i}] missing [destination]"
                    )
                validate_cidr(route["destination"])

        # --- postUp: 可选, str ---
        if K.POST_UP in data:
            if not isinstance(data[K.POST_UP], str):
                raise ValueError(
                    f"WgNetworkConfig: [{K.POST_UP}] must be a string"
                )

        # --- postDown: 可选, str ---
        if K.POST_DOWN in data:
            if not isinstance(data[K.POST_DOWN], str):
                raise ValueError(
                    f"WgNetworkConfig: [{K.POST_DOWN}] must be a string"
                )

    # ── 属性访问器 ──────────────────────────────────────────────────────────

    @property
    def network_name(self) -> str:
        return self._data[self.Key.NETWORK_NAME]

    @property
    def subnet(self) -> str:
        return self._data[self.Key.SUBNET]

    @property
    def listen_port(self) -> int:
        return self._data[self.Key.LISTEN_PORT]

    @property
    def dns_servers(self) -> list:
        return self._data.get(self.Key.DNS_SERVERS, [])

    @property
    def mtu(self) -> int:
        return self._data.get(self.Key.MTU, self.DEFAULT_MTU)

    @property
    def persistent_keepalive(self) -> int:
        return self._data.get(self.Key.PERSISTENT_KEEPALIVE, self.DEFAULT_KEEPALIVE)

    @property
    def routes(self) -> list:
        return self._data.get(self.Key.ROUTES, [])

    @property
    def post_up(self) -> str | None:
        return self._data.get(self.Key.POST_UP, None)

    @property
    def post_down(self) -> str | None:
        return self._data.get(self.Key.POST_DOWN, None)

    def to_dict(self) -> dict:
        """导出为字典。"""
        return dict(self._data)

    @staticmethod
    def from_file(path: str) -> "WgNetworkConfig":
        """从 JSON 文件加载网络配置。

        Args:
            path: JSON 文件路径

        Returns:
            WgNetworkConfig 实例

        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON 解析失败
            ValueError: 校验失败
        """
        with open(path, "r") as f:
            data = json.load(f)
        return WgNetworkConfig(data)


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
