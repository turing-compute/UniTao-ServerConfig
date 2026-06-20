# Feature: Domain Tool — WireGuard 配置管理

## 背景

UniTao-ServerConfig 创建的 VM 之间可能需要组建 VPN 网络。WireGuard 作为示例领域工具，展示 VM 如何通过 Host REST API 完成领域特定的信息发布与获取。

**KVM Host 只是一个数据发布平台**。Host：
- 接收 VM 上报的数据（通过已有的 inventory API）
- 让数据可被检索（通过已有的 inventory API）
- **不包含任何 WireGuard 领域知识** — 不管理 Peer、不分配 IP、不生成密钥

**外部 Orchestrator**（本工具范围外）负责：
- 读取各 VM 发布的公钥
- 决定角色：哪个 VM 是 WG Server（分配 subnet）、哪些是 Client
- 决定每个 VM 的 endpoint 和拓扑
- 从 WG Server 的 inventory 读取 IP 分配结果，回填到各 Client 的 `self.assignedIP`

**VM 侧 Agent** 负责：
- 生成 WireGuard 密钥对，将公钥写入 `wireguard_config.json`
- **WG Server 角色**: Orchestrator 分配 subnet 后，自取 `x.x.x.1`，为 peers 分配剩余 IP
- **Client 角色**: 等待 Orchestrator 回填 `assignedIP`
- 生成配置并激活接口

## 目标

1. **数据层**: 定义 WireGuard 相关数据模型（网络配置、Peer 信息），用于校验和构建配置文件
2. **VM 侧**: WireGuard Agent 预装在 VM image 中，作为 systemd service，负责密钥生成、公钥发布、轮询外部应用下发的配置并激活
3. **Host 无关**: Host 不感知 WireGuard，只用已有的通用 API 存取数据

## 技术选型

- **部署方式**: WireGuard Agent **预装在 VM image 中**，作为 systemd service 运行
- **信息发布**: VM 将公钥写入 `wireguard_config.json`，POST 到 inventory
- **拓扑下发**: Orchestrator 决定角色（Server/Client）、endpoint、拓扑，回填 `self.endpoint` + `peers`
- **IP 分配**: WG Server 被 Orchestrator 分配 subnet 后，自取 `x.x.x.1`，为 peers 分配剩余 IP 并写入各自的 peer 条目中
- **IP 回填**: Orchestrator 读取 WG Server inventory 中的 IP 分配结果，回填到各 Client 的 `self.assignedIP`
- **配置生成**: Client 等待 Orchestrator 回填 assignedIP 后生成 wg.conf 并激活
- **Host 角色**: 纯数据存储与发布，不包含 WireGuard 领域逻辑
- **Agent 依赖**: 仅 Python 3 stdlib + 系统命令 `wg` `ip` `curl`
- **密钥生成**: VM 侧 `wg genkey` / `wg pubkey`

---

## 步骤进度

| 步骤 | 状态 | 内容 |
|------|------|------|
| 1 | `[x]` | `src/rest/api_vm.py` — inventory API：按 name 存储 + 返回文件修改时间戳 |
| 2 | `[x]` | `src/rest/api_vm.py` — VM commit API + `src/kvm/image/prep_image_for_commit.py` + `src/domain/wireguard/prep_image_for_commit.py` — commit 前清理 |
| 3.1 | `[ ]` | `src/domain/wireguard/wg_data.py` — 纯数据模型：IP 工具函数、WgNetworkConfig、WgPeerData |
| 3.2 | `[ ]` | `src/domain/wireguard/wg_config_file.py` — WgConfigFile：wireguard_config.json |
| 3.3 | `[ ]` | `src/domain/wireguard/wg_key_manager.py` — WgKeyManager：VM 侧密钥对管理 |
| 3.4 | `[ ]` | `src/domain/wireguard/wg_config_builder.py` — WgConfigBuilder：wg.conf 配置文本构建 |
| 4 | `[ ]` | `src/domain/wireguard/wg_agent.py` — VM 侧 Agent |
| 5 | `[ ]` | `src/domain/wireguard/wg_agent_service.py` — Agent 的 systemd 化管理 |

> **图例**: `[ ]` 待开始 `[~]` 进行中 `[x]` 已完成 `[-]` 已取消

## 下一步

步骤 1：修改 inventory POST API，支持按请求中的 `name` 字段命名文件。

---

## 详细设计

### 步骤 1: `src/rest/api_vm.py` — Inventory API 扩展

**问题**: 当前 `post_inventory()` 始终以 `{YYYYMMDDTHHMMSSZ}.json` 命名文件。每次都产生新文件，无法按固定名称检索或覆盖更新。

**变更**: POST body 中若包含 `"name"` 字段，则存储为 `{name}.json`（覆盖已有同名文件）。若没有 `"name"`，回退到原有 `{timestamp}.json` 行为，保持向后兼容。

`post_inventory()` 变更:

```python
# 原逻辑:
timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
inv_file = os.path.join(inv_dir, f"{timestamp}.json")

# 新逻辑:
file_name = inv_data.get("name", None)
if file_name and isinstance(file_name, str) and file_name.strip():
    inv_file = os.path.join(inv_dir, f"{file_name}.json")
else:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    inv_file = os.path.join(inv_dir, f"{timestamp}.json")
```

**影响**:
- `inventory_tool.py` 无需修改（它只是透传 JSON 文件内容）
- 已有的 `{timestamp}.json` 文件不受影响
- `GET /api/v1/vms/{name}` 响应中 `inventory` 字段为文件名 → 内容映射，Agent 可以按 `"wireguard_config.json"` 键名直接定位配置

**验证**:
- `POST .../inventory` with `{"name":"test", "data":...}` → 产生 `test.json`
- 再次 POST 同名 → `test.json` 被覆盖，inventory 中只有一个 `test.json`
- `POST .../inventory` without `"name"` → 仍产生 `{timestamp}.json`

---

### 步骤 2: `src/rest/api_vm.py` — VM Commit API

**用途**: 在 WireGuard base image 准备流程中，将已安装 wg_agent 的 VM 磁盘变更提交回 base image。

**工作流**:
```
创建 VM (from base image)
  → 安装系统依赖: apt install wireguard-tools
  → python3 install.py --network wg-mesh --agent wg_agent.py --config wg-mesh.json
     (创建 /opt/unitao/, 复制 agent + config, 创建 systemd unit)
  → python3 prep_image_for_commit.py --network wg-mesh --force
     (WireGuard 级清理: 删除 WG 密钥、agent 运行时产物)
  → python3 prep_image_for_commit.py --force
     (VM 级清理: SSH host key、machine-id、cloud-init state 等)
  → 关机 → virsh destroy
  → POST /api/v1/vms/{vmName}/commit {"disk": 0}
  → base image 即为 "WireGuard-ready" image
```

**前置条件**:
- VM 存在，且 `vmState == "stopped"`
- virsh 中该 VM 不存在（`virsh list --all` 不包含该 VM，即已 destroy）
- VM 的磁盘是 qcow2 格式且有 backing file
- 不满足任一条件返回 400

**端点**: `POST /api/v1/vms/<vmName>/commit`

无请求体。Backing image 从 qemu-img info 自动检测。

处理逻辑:
```python
@vm_bp.route("/<name>/commit", methods=["POST"])
def commit_vm_image(name: str):
    # 1. 验证 VM 存在 + vmState == "stopped"
    # 2. 验证 virsh 中 VM 不存在（已 destroy）
    # 3. 从 VM JSON disks[0] 解析 disk JSON → 获取 qcow2 路径
    # 4. qemu-img info 获取 backing file 路径
    # 5. qemu-img commit <disk_image>
    # 6. 更新 backing image JSON（lastCommitFrom, lastCommitAt）
```

---

### 步骤 3.1: `src/domain/wireguard/wg_data.py` — 纯数据模型

**职责**: 定义 WireGuard 领域的基础数据结构，**只做校验和承载，不涉及系统调用、文件 I/O、文本生成**。

#### 模块内容

```
wg_data.py
├── IP 地址工具函数   — parse_cidr, ip_to_int, int_to_ip, ip_in_subnet, validate_ipv4, validate_cidr
├── WgNetworkConfig   — 网络配置 JSON 校验与承载
└── WgPeerData        — Peer 连接信息校验与承载
```

#### IP 地址工具函数

```python
def parse_cidr(cidr: str) -> tuple[str, int]:
    """'10.200.0.10/24' → ('10.200.0.10', 24)"""

def ip_to_int(ip: str) -> int:
    """'10.200.0.10' → 0x0AC8000A"""

def int_to_ip(num: int) -> str:
    """0x0AC8000A → '10.200.0.10'"""

def ip_in_subnet(ip: str, subnet_cidr: str) -> bool:
    """判断 IP 是否在子网 CIDR 范围内"""

def validate_ipv4(ip: str):
    """校验 IPv4 格式，不合法抛 ValueError"""

def validate_cidr(cidr: str):
    """校验 CIDR 格式，不合法抛 ValueError"""
```

---

#### WgNetworkConfig — 网络配置模型

定义 VM Mesh 网络的参数。此配置文件由**外部应用或管理员**放置在 VM image 中或通过 cloud-init 注入，VM Agent 启动时读取。

**JSON Schema** (在 VM 上的路径由 Agent 配置决定，例如 `/opt/unitao/wg-mesh.json`):

```json
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
```

**类设计**:

```python
class WgNetworkConfig:
    """WireGuard Mesh 网络配置的校验和承载。"""

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

    def __init__(self, data: dict): ...
    def validate(self):
        """校验规则:

        networkName:         必填, 非空 str
        subnet:              必填, 合法 CIDR, netmask 8-30
        listenPort:          必填, int, 1-65535
        dnsServers:          若存在: list[str], 每项合法 IPv4
        mtu:                 若存在: int, 1280-1500
        persistentKeepalive: 若存在: int, 0-65535
        routes:              若存在: list[dict], 每项 .destination 合法 CIDR
        postUp/postDown:     若存在: str
        """

    @property
    def network_name(self) -> str: ...
    @property
    def subnet(self) -> str: ...
    @property
    def listen_port(self) -> int: ...
    @property
    def dns_servers(self) -> list[str]: ...       # 默认 []
    @property
    def mtu(self) -> int: ...                      # 默认 1420
    @property
    def persistent_keepalive(self) -> int: ...     # 默认 25
    @property
    def routes(self) -> list[dict]: ...            # 默认 []
    @property
    def post_up(self) -> str | None: ...
    @property
    def post_down(self) -> str | None: ...

    def to_dict(self) -> dict: ...

    @staticmethod
    def from_file(path: str) -> "WgNetworkConfig": ...
```

---

### 步骤 3.2: `src/domain/wireguard/wg_config_file.py` — Inventory 数据结构

**职责**: 定义 `wireguard_config.json` 的完整格式，VM Agent 和 Orchestrator 通过此结构协作。依赖 `wg_data.py`（WgPeerData）。

**文件**: `wg_config_file.py`（~55 行）

#### WgConfigFile — `wireguard_config.json` 数据结构

VM 和 Orchestrator 通过同一个文件协作。VM 写入 `self` 段，Orchestrator 回填 `peers` 和 `self.assignedIP`。

**JSON 格式** (`wireguard_config.json`):

```json
{
  "name": "wireguard_config",
  "data": {
    "timestamp": "2026-06-18T10:00:00Z",
    "self": {
      "publicKey": "qH3q5X...base64...",
      "id": "vm-mail01",
      "role": "server",
      "endpoint": "192.168.122.10:51820",
      "assignedSubnet": "10.200.0.0/24",
      "assignedIP": "10.200.0.1/24"
    },
    "peers": [
      {
        "publicKey": "abc...",
        "endpoint": "192.168.122.11:51820",
        "assignedIP": "10.200.0.11/32",
        "allowedIPs": ["10.200.0.11/32"]
      }
    ]
  }
}
```

| 段 | 写入方 | 说明 |
|------|------|------|
| `data.timestamp` | 每次更新方 | ISO 8601 UTC 时间戳，每次 POST 时更新 |
| `data.self.publicKey` | VM Agent | VM 的 WireGuard 公钥 |
| `data.self.id` | Orchestrator | VM 在 WireGuard 网络中的标识 |
| `data.self.role` | Orchestrator | `"server"` 或 `"client"` |
| `data.self.endpoint` | Orchestrator | VM 的可达地址 `ip:port` |
| `data.self.assignedSubnet` | Orchestrator | 仅 server: Orchestrator 分配的子网 CIDR |
| `data.self.assignedIP` | Server / Orchestrator | Server 自取 `.1` 写回；Client 由 Orchestrator 回填 |
| `data.peers[*].publicKey` | Orchestrator | Peer 的公钥 |
| `data.peers[*].endpoint` | Orchestrator | Peer 的可达地址 |
| `data.peers[*].assignedIP` | Server / Orchestrator | Server 分配；Client 由 Orchestrator 回填 |

**交互序列**:
1. 所有 VM 写入: `data.self.publicKey`, `data.peers: []`, `data.timestamp` 更新
2. Orchestrator PATCH 回填: `data.self.id`, `data.self.role`, `data.self.endpoint`, `data.peers: [{publicKey, endpoint}, ...]`, `data.timestamp` 更新
   - 若 `role == "server"`: 额外写入 `data.self.assignedSubnet: "10.200.0.0/24"`
3. Server: 轮询发现 `role=="server"` 且 `assignedSubnet` 非空 → 自取 `10.200.0.1/24` 写入 `data.self.assignedIP`
   → 为每个 Peer 分配 IP → 写入 `data.peers[*].assignedIP`, `data.timestamp` 更新
4. Orchestrator 从 Server inventory 读取 IP 分配 → 回填 Client 的 `data.self.assignedIP` + `data.peers[*].assignedIP`, `data.timestamp` 更新
5. Client 轮询发现 IP 齐备 → 生成配置激活接口
6. Agent 通过 `data.timestamp` 判断变更，通过 `data.self.role` 确定行为

**类设计**:

```python
class WgConfigFile:
    """wireguard_config.json 的数据结构。

    Inventory 存储格式:
        {"name": "wireguard_config", "data": {"self": {...}, "peers": [...]}}
    """

    INVENTORY_NAME = "wireguard_config"

    class Key:
        NAME = "name"
        DATA = "data"

    class DataKey:
        TIMESTAMP = "timestamp"
        SELF      = "self"
        PEERS     = "peers"

    class SelfKey:
        PUBLIC_KEY      = "publicKey"
        ID              = "id"
        ROLE            = "role"
        ENDPOINT        = "endpoint"
        ASSIGNED_SUBNET = "assignedSubnet"
        ASSIGNED_IP     = "assignedIP"

    ROLE_SERVER = "server"
    ROLE_CLIENT = "client"

    def __init__(self, inventory_dict: dict):
        """解析 inventory 文件内容。inventory_dict 含 name + data 顶层字段。
        self._data = inventory_dict["data"]
        """

    def validate(self):
        """校验:
        - name: 必须 == "wireguard_config"
        - data: dict, 含 timestamp + self + peers
        - data.timestamp: 必填, ISO 8601 格式字符串
        - data.self.publicKey: 必填, 非空 str
        - data.self.id: 若存在, 非空 str (Orchestrator PATCH 填入)
        - data.self.role: 若存在, "server" 或 "client" (Orchestrator PATCH 填入)
        - data.self.endpoint: 若存在且非空, host:port 格式 (Orchestrator PATCH 填入)
        - data.self.assignedSubnet: 若存在且非空, 合法 CIDR (仅 role=="server")
        - data.self.assignedIP: 若存在且非空, 合法 CIDR
        - data.peers: list, 每项为合法 WgPeerData
        """

    @property
    def timestamp(self) -> str: ...
    @property
    def self_public_key(self) -> str: ...
    @property
    def self_id(self) -> str | None: ...
    @property
    def self_role(self) -> str | None: ...
    @property
    def self_endpoint(self) -> str | None: ...
    @property
    def self_assigned_subnet(self) -> str | None: ...
    @property
    def self_assigned_ip(self) -> str | None: ...
    @property
    def peers(self) -> list[WgPeerData]: ...

    @property
    def is_server(self) -> bool:
        """self.role == "server" """

    def to_dict(self) -> dict:
        """导出为 inventory 格式，自动更新 timestamp 为当前 UTC 时间。
        {"name":"wireguard_config","data":{"timestamp":"...","self":{...},"peers":[...]}}
        """

    @staticmethod
    def from_file(path: str) -> "WgConfigFile": ...

    @staticmethod
    def create_initial(public_key: str) -> "WgConfigFile":
        """VM 首次发布: {"name":"wireguard_config","data":{"timestamp":"...","self":{"publicKey":"..."},"peers":[]}}"""
```

---

### 步骤 3.3: `src/domain/wireguard/wg_key_manager.py` — 密钥管理

**职责**: VM 侧 WireGuard 密钥对生成与管理。通过系统命令 `wg genkey` / `wg pubkey` 操作。**只在 VM 侧使用**。

**文件**: `wg_key_manager.py`（~85 行）

```python
class WgKeyManager:
    """VM 侧 WireGuard 密钥对管理器。"""

    def __init__(self, network: str, key_dir: str = "/etc/wireguard"):
        """私钥路径: {key_dir}/{network}/private.key"""

    def private_key_path(self) -> str: ...
    def public_key_path(self) -> str: ...
    def generate_private_key(self) -> str:
        """调用 wg genkey，写入 private.key (权限 600)。"""
    def derive_public_key(self, private_key: str) -> str:
        """echo <key> | wg pubkey"""
    def generate_keypair(self, force: bool = False) -> tuple[str, str]:
        """生成 (private_key, public_key)，force=False 时已存在则直接加载。"""
    def keys_exist(self) -> bool: ...
    def load_private_key(self) -> str: ...
    def load_public_key(self) -> str: ...
    @staticmethod
    def get_interface_public_key(iface: str) -> str | None: ...
    @staticmethod
    def interface_exists(iface: str) -> bool: ...
```

---

### 回到步骤 3.1（续）: `wg_data.py`

#### WgPeerData — Peer 连接信息

表示 Orchestrator 下发的一个 Peer。位于 `wireguard_config.json` 的 `peers` 数组中。

**JSON 格式** (外部应用 → VM inventory):

```json
{
  "publicKey": "qH3q5X...base64...",
  "endpoint": "192.168.122.11:51820",
  "assignedIP": "10.200.0.11/32",
  "allowedIPs": ["10.200.0.11/32"]
}
```

| 字段 | 说明 |
|------|------|
| `publicKey` | Peer 的 WireGuard 公钥 (44 字符 base64) |
| `endpoint` | Peer 的可达地址 `ip:port` |
| `assignedIP` | Peer 在 VPN 子网中的 IP (CIDR /32) |
| `allowedIPs` | 通过该 Peer 可到达的 IP 范围 |

**类设计**:

```python
class WgPeerData:
    """WireGuard Peer 配置数据校验和承载。"""

    class Key:
        PUBLIC_KEY   = "publicKey"
        ENDPOINT     = "endpoint"
        ASSIGNED_IP  = "assignedIP"
        ALLOWED_IPS  = "allowedIPs"

    def __init__(self, data: dict): ...
    def validate(self):
        """逐字段校验:
        - publicKey:    非空 str, 长度 44, base64 字符集 (A-Za-z0-9+/)
        - endpoint:     host:port 格式, port 1-65535
        - assignedIP:   合法 CIDR
        - allowedIPs:   list[str], 每项合法 CIDR, 非空
        """

    @property
    def public_key(self) -> str: ...
    @property
    def endpoint(self) -> str: ...             # "192.168.122.11:51820"
    @property
    def assigned_ip(self) -> str: ...          # "10.200.0.11/32"
    @property
    def assigned_ip_address(self) -> str: ...  # "10.200.0.11"
    @property
    def allowed_ips(self) -> list[str]: ...

    def to_dict(self) -> dict: ...
```

---

### 步骤 3.4: `src/domain/wireguard/wg_config_builder.py` — 配置构建

**职责**: 将数据模型（WgNetworkConfig + WgPeerData）构建为 `wg.conf` 文本。**纯文本生成，不涉及 I/O**。

**依赖**: `wg_data.py`（WgNetworkConfig, WgPeerData）

**文件**: `wg_config_builder.py`（~65 行）

将数据模型构建为 `wg.conf` 文本。**不读写文件，只生成字符串**。

```python
class WgConfigBuilder:
    """WireGuard 配置文件构建器。"""

    @staticmethod
    def build_interface_section(
        private_key: str,
        address: str,              # CIDR
        listen_port: int,
        dns_servers: list[str] = None,
        mtu: int = 1420,
        post_up: str = None,
        post_down: str = None,
    ) -> str:
        """生成 [Interface] 段。

        输出示例:
            [Interface]
            PrivateKey = <key>
            Address = 10.200.0.10/32
            ListenPort = 51820
            DNS = 10.200.0.1
            MTU = 1420
        """

    @staticmethod
    def build_peer_section(
        peer: WgPeerData,
        persistent_keepalive: int = 25,
    ) -> str:
        """为单个 Peer 生成 [Peer] 段。

        输出示例:
            # Peer: vm-web01
            [Peer]
            PublicKey = abc123...
            Endpoint = 192.168.122.11:51820
            AllowedIPs = 10.200.0.11/32
            PersistentKeepalive = 25
        """

    @staticmethod
    def build_vm_config(
        network: WgNetworkConfig,
        self_ip: str,            # CIDR, "10.200.0.10/32"
        private_key: str,
        peers: list[WgPeerData],
    ) -> str:
        """生成 VM 侧完整 wg.conf。

        结构:
            [Interface]
            PrivateKey = <private_key>
            Address = <self_ip>
            ListenPort = <network.listen_port>
            ... (DNS, MTU, PostUp, PostDown 按 network 配置)

            # Peer: <endpoint>
            [Peer]
            ... (每个 peer 一段)

        peers 来自外部应用下发的 wireguard_config.json。
        """
```

**VM Mesh 配置示例**（VM-A 视角，3 个 VM）:

```ini
[Interface]
PrivateKey = <vm-a-private-key>
Address = 10.200.0.10/32
ListenPort = 51820
MTU = 1420

# Peer: vm-web01
[Peer]
PublicKey = <vm-b-public-key>
Endpoint = 192.168.122.11:51820
AllowedIPs = 10.200.0.11/32
PersistentKeepalive = 25

# Peer: vm-db01
[Peer]
PublicKey = <vm-c-public-key>
Endpoint = 192.168.122.12:51820
AllowedIPs = 10.200.0.12/32
PersistentKeepalive = 25
```

---

#### 步骤 3.1–3.4 文件清单

```
src/rest/
    api_vm.py              # 步骤 1: inventory name 存储 + 步骤 2: commit API

src/kvm/image/
    prep_image_for_commit.py          # 步骤 2: VM 级 commit 前清理

src/domain/wireguard/
    __init__.py            # 空 (已存在)
    wg_data.py             # 步骤 3.1: IP 工具 + WgNetworkConfig + WgPeerData
    wg_config_file.py      # 步骤 3.2: WgConfigFile
    wg_key_manager.py      # 步骤 3.3: WgKeyManager
    wg_config_builder.py   # 步骤 3.4: WgConfigBuilder
    install.py            # 步骤 2: 安装 agent 到 image
    prep_image_for_commit.py          # 步骤 2: WireGuard 级 commit 前清理
    wg_agent.py            # 步骤 4: VM Agent
    wg_agent_service.py    # 步骤 5: systemd 化
```

**依赖关系**: `wg_config_file` → `wg_data`（WgPeerData）；`wg_config_builder` → `wg_data`（WgNetworkConfig, WgPeerData）

#### 步骤 2 验证方式

1. **Import**: 四个模块均可独立 import，无循环依赖
2. **校验**: 合法 JSON → `__init__` 通过；非法 JSON → ValueError 带字段名
3. **密钥**: Linux 环境 `wg genkey` / `wg pubkey` 生成 44 字符 base64 密钥对
4. **IP 工具**: `ip_to_int` ↔ `int_to_ip` 往返一致
5. **配置构建**: `build_vm_config()` 输出符合 `wg-quick` 格式

---

### 步骤 4: `src/domain/wireguard/wg_agent.py`

VM 侧 WireGuard Agent。预装在 VM image 中。Agent 根据 Orchestrator 分配的角色执行不同逻辑：**Server**（有 `assignedSubnet`）负责 IP 分配，**Client** 等待 Orchestrator 回填。

#### 4.1 Agent 工作流程

Agent 根据 Orchestrator 分配的角色执行不同逻辑。

```
[1] 读取网络配置文件 → WgNetworkConfig
    路径: /opt/unitao/wg-mesh.json (由 image 构建时或 cloud-init 注入)

[2] 生成/加载密钥对 → WgKeyManager.generate_keypair()
    ※ 首次启动时全新生成，确保每台 VM 实例密钥唯一，不复用 image 中的密钥
    ※ 后续重启: keys_exist() → 直接加载已有密钥，保持身份一致

[3] 读取 inventory.json → hostApiUrl, vmId

[4] 第一轮 — 用刚生成的公钥创建并发布 wireguard_config.json:
    WgConfigFile.create_initial(public_key)
    → POST /api/v1/vms/{vmId}/inventory
    {"name":"wireguard_config","data":{"self":{"publicKey":"qH3..."},"peers":[]}}
    ※ 密钥先生成，再填入文件并发布，保证 image 中不残留密钥

[5] 第二轮 — 轮询等待 Orchestrator PATCH:
    定期 GET .../inventory/wireguard_config.json
    → 检查 self.role 是否非空 (Orchestrator 已 PATCH)
    → role=="server" → Server 路径; role=="client" → Client 路径

[6] 分支:

    ┌─ Server 路径 ──────────────────────────────────────┐
    │ a. 自取 IP: self.assignedSubnet = "10.200.0.0/24"  │
    │    → 分配 self.assignedIP = "10.200.0.1/24"        │
    │ b. 为每个 Peer 分配 IP:                             │
    │    → peers[0].assignedIP = "10.200.0.2/32"         │
    │    → peers[1].assignedIP = "10.200.0.3/32"         │
    │ c. POST 写回 inventory (更新 timestamp)             │
    │ d. 生成配置: WgConfigBuilder.build_vm_config(...)   │
    │    → wg-quick up wg-mesh                           │
    └────────────────────────────────────────────────────┘

    ┌─ Client 路径 ──────────────────────────────────────┐
    │ a. 继续轮询, 等待 Orchestrator 回填:                │
    │    → peers[*].assignedIP 是否全部非空?              │
    │    → self.assignedIP 是否非空?                      │
    │ b. IP 齐备后:                                       │
    │    WgConfigBuilder.build_vm_config(...)             │
    │    → wg-quick up wg-mesh                           │
    └────────────────────────────────────────────────────┘

[7] 继续轮询, Peer 变更时 wg syncconf 增量更新
```

#### 4.2 Agent 命令行

```bash
# 基本用法 (发布公钥并进入轮询循环)
python3 /opt/unitao/wg_agent.py --network wg-mesh

# 指定网络配置文件路径
python3 /opt/unitao/wg_agent.py --network wg-mesh --config /opt/unitao/wg-mesh.json

# 指定 inventory 配置文件路径
python3 /opt/unitao/wg_agent.py --network wg-mesh --inventory /opt/unitao-server-config/inventory.json

# 仅发布公钥，不轮询 (one-shot 模式)
python3 /opt/unitao/wg_agent.py --network wg-mesh --publish-only

# 仅检查并应用配置一次，不循环 (用于 timer 触发)
python3 /opt/unitao/wg_agent.py --network wg-mesh --once
```

#### 4.3 Agent 特性

- **双角色**: Server 角色 (有 `assignedSubnet`) 自取 `.1` 并分配 Peer IP；Client 角色等待 Orchestrator 回填
- **密钥安全**: 首次启动时生成密钥对，不预置在 image 中，避免克隆 VM 共享同一密钥
- **轮询**: 定期（默认 30 秒）通过 `inventory_tool.py --get wireguard_config.json` 检查配置变更
- **幂等**: 重复运行安全 — 检测已有配置，仅在 Peer 列表变更时更新
- **增量更新**: Peer 变更时使用 `wg syncconf` 而非重启接口，避免中断已有连接
- **重试**: Host API 不可达时等待下次轮询，不立即失败
- **systemd 支持**: `--once` 模式配合 systemd timer 定期触发；`--publish-only` 在 boot 时一次性发布公钥

---

### 步骤 5: `src/domain/wireguard/wg_agent_service.py`

Agent 的 systemd 化管理和安装脚本。

#### 5.1 systemd unit 文件

```ini
# /etc/systemd/system/wg-agent@.service
[Unit]
Description=WireGuard Mesh Agent for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /opt/unitao/wg_agent.py --network %i
ExecStop=/usr/bin/wg-quick down %i
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

#### 5.2 安装脚本

`wg_agent_service.py` 提供：
- `install(network: str)` — 复制 agent 到 `/opt/unitao/`，创建 systemd unit，enable+start
- `uninstall(network: str)` — stop+disable+删除 unit 文件
- `status(network: str)` — 检查 agent 和 wg 接口状态

---

## 数据流

```
┌─ 第一轮: 所有 VM 发布公钥 ────────────────────────────────────────────┐
│                                                                         │
│  vm-mail01: POST {"name":"wireguard_config",                          │
│    "data":{"self":{"publicKey":"qH3..."},"peers":[]}}                 │
│  vm-web01:  POST {"name":"wireguard_config",                          │
│    "data":{"self":{"publicKey":"abc..."},"peers":[]}}                 │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘

┌─ 第二轮: Orchestrator 回填拓扑 (本工具范围外) ────────────────────────┐
│                                                                         │
│  ① 决定角色: vm-mail01 = server, vm-web01 = client                     │
│  ② PATCH vm-mail01:                                                    │
│      {"name":"wireguard_config",                                       │
│       "data":{"self":{"publicKey":"qH3...",                            │
│                "id":"vm-mail01","role":"server",                       │
│                "endpoint":"192.168.122.10:51820",                      │
│                "assignedSubnet":"10.200.0.0/24"},                      │
│       "peers":[{"publicKey":"abc...",                                  │
│                  "endpoint":"192.168.122.11:51820"}]}}                 │
│  ③ PATCH vm-web01:                                                    │
│      {"name":"wireguard_config",                                       │
│       "data":{"self":{"publicKey":"abc...",                            │
│                "id":"vm-web01","role":"client",                        │
│                "endpoint":"192.168.122.11:51820"},                     │
│       "peers":[{"publicKey":"qH3...",                                  │
│                  "endpoint":"192.168.122.10:51820"}]}}                 │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘

┌─ 第三轮: WG Server 分配 IP ───────────────────────────────────────────┐
│                                                                         │
│  vm-mail01 (Server):                                                    │
│    发现 assignedSubnet = "10.200.0.0/24"                                │
│    → self.assignedIP = "10.200.0.1/24"                                 │
│    → peers[0].assignedIP = "10.200.0.2/32"  (vm-web01)                 │
│    → POST 写回 inventory                                                │
│    → wg-quick up wg-mesh                                               │
│                                                                         │
│  vm-web01 (Client):                                                     │
│    继续轮询, 等待 Orchestrator 回填 IP...                               │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘

┌─ 第四轮: Orchestrator 回填 Client IP ─────────────────────────────────┐
│                                                                         │
│  读取 vm-mail01 inventory → peers[0].assignedIP = "10.200.0.2/32"     │
│  → 回填 vm-web01:                                                      │
│      self.assignedIP = "10.200.0.2/32"                                 │
│      peers[0].assignedIP = "10.200.0.1/32"  (Server 的 IP)            │
│                                                                         │
│  vm-web01 轮询发现 IP 齐备 → wg-quick up wg-mesh                       │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 架构图

```
┌──────────────────────────────────────────────────────────────┐
│ KVM Host (纯数据平台，无 WireGuard 领域知识)                  │
│                                                               │
│  ┌────────────────────────┐                                   │
│  │ REST API (通用, 已有)   │                                   │
│  │ POST .../inventory     │  ← VM 发布 + Orchestrator 回填   │
│  │ GET  .../inventory     │  ← VM 轮询                       │
│  └──┬─────────────────────┘                                   │
│     │                                                         │
│  ┌──▼──────────────────────────────────────────────────────┐ │
│  │ Inventory 目录                                            │ │
│  │ vm-mail01/.../inventory/wireguard_config.json            │ │
│  │ vm-web01/.../inventory/wireguard_config.json             │ │
│  │ vm-db01/.../inventory/wireguard_config.json              │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ※ Host 不安装 WireGuard, 不生成密钥, 不管理 Peer            │
└────────────────────────┬──────────────────────────────────────┘
                         │ 管理网络 (Bridge)
            ┌────────────┼────────────┐
            │            │            │
      ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼─────┐
      │ VM-A      │ │ VM-B      │ │ VM-C      │
      │           │ │           │ │           │
      │ wg_agent  │ │ wg_agent  │ │ wg_agent  │
      │ ① POST公钥│ │ ① POST公钥│ │ ① POST公钥│
      │ ② poll拓朴│ │ ② poll拓朴│ │ ② poll拓朴│
      │ ③ self-IP │ │ ③ self-IP │ │ ③ self-IP │
      │ ④ 发现peer│ │ ④ 发现peer│ │ ④ 发现peer│
      │ ⑤ apply   │ │ ⑤ apply   │ │ ⑤ apply   │
      │           │ │           │ │           │
      │ wg-mesh   │ │ wg-mesh   │ │ wg-mesh   │
      │ .10/32    │ │ .11/32    │ │ .12/32    │
      └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
            │             │             │
            └── WireGuard Mesh ─────────┘

┌──────────────────────────────────────────────────────────────┐
│ Orchestrator (本工具范围外)                                   │
│                                                               │
│  ① 读取各 VM 的 publicKey                                     │
│  ② 决定 endpoint 和拓扑 (谁与谁 Peer)                         │
│  ③ 回填 self.endpoint + peers (不填 IP)                       │
│                                                               │
│  ※ IP 由 WG Server 分配, Orchestrator 回填给 Client          │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 向后兼容

| 场景 | 行为 |
|------|------|
| 旧 VM image（无 wg_agent） | 无 WireGuard 行为，VM 正常运行 |
| Host API 不可达 | Agent 重试后日志告警，不阻塞 VM 启动 |
| wireguard_config.json 不存在 | Agent 持续轮询等待，wg 接口暂不启动 |
| Agent 重复运行 | 幂等，检测已有配置，仅 Peer 列表变更时更新 |
| Orchestrator 新增 Peer | Agent 下次轮询发现新 Peer，增量更新 wg 接口 |

---

## 验证方式

1. **数据模型**: 合法/非法 JSON 校验按预期通过/失败
2. **密钥生成**: `wg genkey` → `wg pubkey` 推导正确
3. **Agent 发布**: VM POST wireguard_config.json (含 self, peers=[]) → Orchestrator 可读取公钥
4. **配置生成**: `build_vm_config()` 输出符合 `wg-quick` 格式，Peer 段完整
5. **端到端**: 外部应用下发配置 → 两台 VM Agent 轮询获取 → 互 ping VPN IP 通
