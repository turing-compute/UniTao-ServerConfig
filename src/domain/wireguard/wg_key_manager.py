#!/usr/bin/env python3

"""VM 侧 WireGuard 密钥对管理器。

通过系统命令 wg genkey / wg pubkey 生成和管理密钥对。
密钥文件存储在 /etc/wireguard/{network}/ 下。

首次启动 vs 重启的判断: 密钥文件是否存在。
    - image 构建时 prep_image_for_commit.py 已删除密钥文件
    - clone 出来的 VM 首次启动: private.key 不存在 → 生成新密钥
    - 重启: private.key 存在 → 加载已有密钥，身份不变
"""

import os
import subprocess


class WgKeyManager:
    """VM 侧 WireGuard 密钥对管理器。

    用法:
        km = WgKeyManager("wg-mesh")
        private_key, public_key = km.generate_keypair()
    """

    def __init__(self, network: str, key_dir: str = "/etc/wireguard"):
        """初始化密钥管理器。

        Args:
            network: WireGuard 网络名称 (e.g. "wg-mesh")
            key_dir: 密钥存储根目录，默认 /etc/wireguard

        密钥路径:
            私钥: {key_dir}/{network}/private.key
            公钥: {key_dir}/{network}/public.key
        """
        if not network or not network.strip():
            raise ValueError("WgKeyManager: network name must be a non-empty string")
        self._network = network
        self._key_dir = key_dir
        self._network_dir = os.path.join(key_dir, network)

    @property
    def network(self) -> str:
        return self._network

    def private_key_path(self) -> str:
        """私钥文件路径。"""
        return os.path.join(self._network_dir, "private.key")

    def public_key_path(self) -> str:
        """公钥文件路径。"""
        return os.path.join(self._network_dir, "public.key")

    def keys_exist(self) -> bool:
        """密钥对是否已存在 (检查私钥文件)。"""
        return os.path.isfile(self.private_key_path())

    # ── 密钥生成 ──────────────────────────────────────────────────────────

    def generate_private_key(self) -> str:
        """调用 wg genkey 生成私钥，写入 private.key (权限 600)。

        Returns:
            生成的私钥字符串
        """
        result = subprocess.run(
            ["wg", "genkey"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemError(
                f"wg genkey failed with code {result.returncode}: {result.stderr.strip()}"
            )
        private_key = result.stdout.strip()
        if not private_key:
            raise SystemError("wg genkey returned empty key")

        # 确保目录存在
        os.makedirs(self._network_dir, exist_ok=True)

        # 写入私钥，设置权限 600
        fd = os.open(self.private_key_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (private_key + "\n").encode("utf-8"))
        finally:
            os.close(fd)

        return private_key

    def derive_public_key(self, private_key: str) -> str:
        """从私钥推导公钥：通过 stdin 传入私钥，调用 wg pubkey。

        Args:
            private_key: 私钥字符串

        Returns:
            公钥字符串 (44 字符 base64)
        """
        if not private_key or not private_key.strip():
            raise ValueError("derive_public_key: private_key must be a non-empty string")

        result = subprocess.run(
            ["wg", "pubkey"],
            input=private_key.strip(),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemError(
                f"wg pubkey failed with code {result.returncode}: {result.stderr.strip()}"
            )
        public_key = result.stdout.strip()
        if not public_key:
            raise SystemError("wg pubkey returned empty key")
        return public_key

    def generate_keypair(self, force: bool = False) -> tuple:
        """生成或加载密钥对。

        force=False 时:
            - 密钥已存在 → 直接加载
            - 密钥不存在 → 生成新密钥对（首次启动）
        force=True 时:
            - 强制重新生成密钥对

        同时缓存公钥到 public.key 以便后续快速读取。

        Returns:
            (private_key, public_key) 元组
        """
        if not force and self.keys_exist():
            return self.load_private_key(), self.load_public_key()

        private_key = self.generate_private_key()
        public_key = self.derive_public_key(private_key)

        # 缓存公钥
        fd = os.open(self.public_key_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, (public_key + "\n").encode("utf-8"))
        finally:
            os.close(fd)

        return private_key, public_key

    # ── 密钥加载 ──────────────────────────────────────────────────────────

    def load_private_key(self) -> str:
        """从文件加载私钥。

        Returns:
            私钥字符串
        """
        path = self.private_key_path()
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Private key not found: {path}")
        with open(path, "r") as f:
            key = f.read().strip()
        if not key:
            raise ValueError(f"Private key file is empty: {path}")
        return key

    def load_public_key(self) -> str:
        """从文件加载公钥。若缓存不存在，从私钥推导。

        Returns:
            公钥字符串
        """
        path = self.public_key_path()
        if os.path.isfile(path):
            with open(path, "r") as f:
                key = f.read().strip()
            if key:
                return key

        # 缓存不存在，从私钥推导
        private_key = self.load_private_key()
        public_key = self.derive_public_key(private_key)

        # 写入缓存
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, (public_key + "\n").encode("utf-8"))
        finally:
            os.close(fd)

        return public_key

    # ── 接口查询 (静态，不依赖密钥目录) ────────────────────────────────────

    @staticmethod
    def get_interface_public_key(iface: str) -> str | None:
        """查询本地 WireGuard 接口的公钥。

        Args:
            iface: WireGuard 接口名，如 "wg-mesh"

        Returns:
            公钥字符串，接口不存在或未运行时返回 None
        """
        try:
            result = subprocess.run(
                ["wg", "show", iface, "public-key"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return None
        if result.returncode != 0:
            return None
        key = result.stdout.strip()
        return key if key else None

    @staticmethod
    def interface_exists(iface: str) -> bool:
        """判断 WireGuard 接口是否存在 (通过 ip link show)。

        Args:
            iface: 接口名

        Returns:
            True 如果接口存在
        """
        try:
            result = subprocess.run(
                ["ip", "link", "show", iface],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return result.returncode == 0
