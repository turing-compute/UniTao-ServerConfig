#!/usr/bin/env python3

"""WireGuard 配置文件构建器。

将数据模型（WgNetworkConfig + WgPeerData）构建为 wg.conf 文本。
纯文本生成，不涉及文件 I/O。

依赖: wg_data.py (WgNetworkConfig, WgPeerData)
"""

from domain.wireguard.wg_data import WgNetworkConfig


class WgConfigBuilder:
    """WireGuard 配置文件构建器。

    用法:
        conf_text = WgConfigBuilder.build_vm_config(network, self_ip, private_key, peers)
    """

    @staticmethod
    def build_interface_section(
        private_key: str,
        address: str,
        listen_port: int,
        dns_servers: list = None,
        mtu: int = 1420,
        post_up: str = None,
        post_down: str = None,
    ) -> str:
        """生成 [Interface] 段。

        Args:
            private_key: WireGuard 私钥
            address:     VM 在 VPN 中的地址 (CIDR 格式，如 "10.200.0.1/24")
            listen_port: 监听端口
            dns_servers: DNS 服务器 IP 列表 (可选)
            mtu:         MTU 值 (默认 1420)
            post_up:     PostUp 脚本 (可选)
            post_down:   PostDown 脚本 (可选)

        Returns:
            [Interface] 段文本

        输出示例:
            [Interface]
            PrivateKey = <key>
            Address = 10.200.0.10/32
            ListenPort = 51820
            DNS = 10.200.0.1
            MTU = 1420
        """
        if dns_servers is None:
            dns_servers = []

        lines = []
        lines.append("[Interface]")
        lines.append(f"PrivateKey = {private_key}")
        lines.append(f"Address = {address}")
        lines.append(f"ListenPort = {listen_port}")

        if dns_servers:
            for dns in dns_servers:
                lines.append(f"DNS = {dns}")

        lines.append(f"MTU = {mtu}")

        if post_up:
            items = post_up if isinstance(post_up, list) else [post_up]
            for cmd in items:
                lines.append(f"PostUp = {cmd}")
        if post_down:
            items = post_down if isinstance(post_down, list) else [post_down]
            for cmd in items:
                lines.append(f"PostDown = {cmd}")

        return "\n".join(lines)

    @staticmethod
    def build_peer_section(peer: dict, persistent_keepalive: int = 25) -> str | None:
        """为单个 Peer 生成 [Peer] 段。

        Args:
            peer:                  Peer dict {publicKey, ip, endpoint}
            persistent_keepalive:  保活间隔秒数 (默认 25)

        Returns:
            [Peer] 段文本，如果缺少必要的 publicKey 则返回 None
        """
        pubkey = peer.get("publicKey", "")
        if not pubkey:
            return None

        # disabled: skip this peer entirely
        if peer.get("disabled", False):
            return None

        endpoint = peer.get("endpoint", None)
        ip = peer.get("ip", "")
        allowed_ips = peer.get("allowed_ips", None)
        if allowed_ips is None and ip:
            allowed_ips = [ip]

        lines = []

        # Comment line: prefer description, fall back to id or endpoint
        desc = peer.get("description", "") or peer.get("comment", "")
        peer_id = peer.get("id", "") or peer.get("peer-id", "")
        if desc:
            lines.append(f"# {desc}")
        elif peer_id:
            lines.append(f"# Peer {peer_id}")

        lines.append("[Peer]")
        lines.append(f"PublicKey = {pubkey}")

        # PresharedKey
        psk = peer.get("presharedKey", "")
        if psk:
            lines.append(f"PresharedKey = {psk}")

        if endpoint:
            lines.append(f"Endpoint = {endpoint}")

        if allowed_ips:
            lines.append(f"AllowedIPs = {', '.join(allowed_ips)}")

        # Peer-level keepalive overrides global default
        keepalive = peer.get("persistentKeepalive", persistent_keepalive)
        if keepalive > 0:
            lines.append(f"PersistentKeepalive = {keepalive}")

        return "\n".join(lines)

    @staticmethod
    def build_vm_config(
        network: WgNetworkConfig,
        private_key: str,
    ) -> str:
        """生成 VM 侧完整 wg.conf。

        Args:
            network:     WgNetworkConfig 实例
            private_key: VM 私钥

        Returns:
            完整 wg.conf 文本
        """
        sections = []

        # [Interface]
        iface = WgConfigBuilder.build_interface_section(
            private_key=private_key,
            address=network.assigned_ip,
            listen_port=network.listen_port,
            dns_servers=network.dns_servers,
            post_up=network.post_up,
            post_down=network.post_down,
        )
        sections.append(iface)

        # [Peer] x N（跳过缺少 publicKey 的无效 peer）
        for peer_dict in network.peers:
            peer_section = WgConfigBuilder.build_peer_section(peer=peer_dict)
            if peer_section:
                sections.append("")
                sections.append(peer_section)

        return "\n".join(sections)
