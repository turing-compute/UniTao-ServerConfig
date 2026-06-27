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
            lines.append(f"PostUp = {post_up}")
        if post_down:
            lines.append(f"PostDown = {post_down}")

        return "\n".join(lines)

    @staticmethod
    def build_peer_section(peer: dict, persistent_keepalive: int = 25) -> str:
        """为单个 Peer 生成 [Peer] 段。

        Args:
            peer:                  Peer dict {public_key, endpoint, assigned_id, allowed_ips}
            persistent_keepalive:  保活间隔秒数 (默认 25)

        Returns:
            [Peer] 段文本
        """
        pubkey = peer.get("public_key", "")
        endpoint = peer.get("endpoint", None)
        allowed_ips = peer.get("allowed_ips", [])

        lines = []

        comment = endpoint if endpoint else pubkey[:12]
        lines.append(f"# Peer: {comment}")

        lines.append("[Peer]")
        lines.append(f"PublicKey = {pubkey}")

        if endpoint:
            lines.append(f"Endpoint = {endpoint}")

        if allowed_ips:
            lines.append(f"AllowedIPs = {', '.join(allowed_ips)}")

        lines.append(f"PersistentKeepalive = {persistent_keepalive}")

        return "\n".join(lines)

    @staticmethod
    def build_vm_config(
        network: WgNetworkConfig,
        self_ip: str,
        private_key: str,
        peers: list,
    ) -> str:
        """生成 VM 侧完整 wg.conf。

        Args:
            network:     WgNetworkConfig 实例
            self_ip:     VM 自身 VPN 地址 (CIDR)
            private_key: VM 私钥
            peers:       Peer 列表 [{public_key, endpoint, assigned_id, allowed_ips}, ...]

        Returns:
            完整 wg.conf 文本
        """
        sections = []

        # [Interface]
        iface = WgConfigBuilder.build_interface_section(
            private_key=private_key,
            address=self_ip,
            listen_port=network.listen_port,
            dns_servers=network.dns_servers,
            mtu=network.mtu,
            post_up=network.post_up,
            post_down=network.post_down,
        )
        sections.append(iface)

        # [Peer] x N
        for peer_dict in peers:
            sections.append("")
            peer_section = WgConfigBuilder.build_peer_section(
                peer=peer_dict,
                persistent_keepalive=network.persistent_keepalive,
            )
            sections.append(peer_section)

        return "\n".join(sections)
