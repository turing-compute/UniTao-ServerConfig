# WireGuard 领域工具

VM Mesh VPN 的 WireGuard 配置管理。所有 peer 对等，Orchestrator 统一规划拓扑和 IP 分配。

## 架构

```
┌──────────────────────────────────────────────────┐
│ Orchestrator (外部)                               │
│   ① 读取各 VM 的 wireguard_network.json          │
│      (含 selfIp, peers, publicKey)               │
│   ② 决定拓扑 + 分配 IP                             │
│   ③ 写入 wireguard_network_inv.json 到各 VM       │
│      inventory                                     │
└────────────────────┬─────────────────────────────┘
                     │ Host REST API (通用，已有)
                     ▼
┌──────────────────────────────────────────────────┐
│ Host Inventory                                    │
│   vm-*/inventory/wireguard_network_inv.json       │
└────────────────────┬─────────────────────────────┘
                     │ VM Agent 轮询 (只读)
                     ▼
┌──────────────────────────────────────────────────┐
│ VM: wg_agent.py                                   │
│   ① 加载 wireguard_network.json                   │
│   ② 无密钥 → 生成密钥 → 回填 publicKey             │
│   ③ 轮询 inventory → 拉取 wireguard_network_inv.json│
│   ④ 比较 timestamp → 合并 network 字段到本地        │
│   ⑤ 生成 wg.conf → wg-quick up                    │
└──────────────────────────────────────────────────┘
```

## 文件说明

| 文件 | 运行位置 | 用途 |
|------|---------|------|
| `wg_data.py` | 任意 | IP 工具函数 + WgNetworkConfig + WgNetworkInv + WgPeerData 数据模型 |
| `wg_key_manager.py` | VM | 密钥对生成与管理 (wg genkey/pubkey) |
| `wg_config_builder.py` | 任意 | 将 WgNetworkConfig 构建为 `wg.conf` 文本 |
| `wg_agent.py` | **VM** | Agent 主程序：生成密钥、轮询 inventory、合并配置、激活接口 |
| `wg_agent_service.py` | **VM** | systemd 服务安装/卸载/状态检查 |
| `install.py` | image 构建时 | 将 agent 安装到 VM image 中 |
| `prep_image_for_commit.py` | image 构建时 | commit 前清除 WireGuard 密钥和运行时文件 |

## 数据流

### 两个配置文件

**`wireguard_network.json`** — 本地配置（Agent 读写，首运行自动创建）：

```json
{
  "data": {
    "public_keys": {
      "primary": "b8TCwHh9...44-char-base64..."
    },
    "network": {
      "assigned_ip": "10.200.0.1/32",
      "listen_port": 51820,
      "dns_servers": ["8.8.8.8"],
      "peers": [
        {
          "publicKey": "aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd=",
          "ip": "10.200.0.2/32",
          "endpoint": "192.168.1.105:51820",
          "allowed_ips": [
            "10.200.0.2/32"
          ]
        }
      ]
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `data.public_keys.primary` | Agent 生成的公钥（44 字符 base64），首运行回填 |
| `data.network.assigned_ip` | Orchestrator 分配的 VPN IP（CIDR 格式） |
| `data.network.listen_port` | WireGuard 监听端口 |
| `data.network.dns_servers` | DNS 服务器列表 |
| `data.network.peers[]` | Peer 列表 |

**Peer 格式**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `publicKey` | string | 是 | WireGuard 公钥，44 字符 base64 |
| `endpoint` | string | 是 | 可达地址 `host:port` |
| `ip` | string | 否 | 单个 VPN IP（CIDR），等价于 `allowed_ips: [ip]` |
| `allowed_ips` | list[string] | 否 | 可达 IP 范围列表（CIDR），默认使用 `ip` |
| `persistentKeepalive` | int | 否 | 保活包间隔（秒），NAT 穿透用，默认 25 |
| `presharedKey` | string | 否 | 预共享密钥（PSK），后量子保护 |
| `description` / `comment` | string | 否 | 备注文本，写入 wg.conf 注释行 |
| `disabled` | bool | 否 | 为 `true` 时跳过该 peer（不写入 wg.conf） |
| `id` / `peer-id` | string/int | 否 | 内部标识符，写入 wg.conf 注释行 |

Orchestrator 通过 `PATCH /api/v1/vms/{name}/inventory/wireguard_network.json` 更新 `data.network`，`data.public_keys` 不会被修改。

### Agent 工作流程

```
Agent 启动:
  ① 加载 wireguard_network.json → WgNetworkConfig
  ② 密钥不存在 → wg genkey → 回填 publicKey 到 JSON
  ③ 进入死循环:
     a. inventory_tool.py --get wireguard_network_inv.json
     b. 如果 inv.timestamp != local.timestamp
        → 把 inv.network 字段合并到 wireguard_network.json
     c. 如果 selfIp 已分配
        → 生成 wg.conf → wg-quick up (或 wg syncconf)
     d. sleep(poll_interval)
```

## 部署

### 1. 制作 WireGuard-ready base image

在 VM image 构建流程中：

```bash
# 在 VM 内安装 wireguard-tools
apt install wireguard-tools

# 安装 agent 到 image (生成 wg_agent.conf + systemd 服务)
python3 install.py --network-config ./wg-mesh.json

# WireGuard 级清理 (删除密钥)
python3 prep_image_for_commit.py --network wg-mesh --force

# VM 级清理 (SSH host key, machine-id, cloud-init 等)
python3 /path/to/kvm/image/prep_image_for_commit.py --force

# 关机 → virsh destroy → commit image
# POST /api/v1/vms/{vmName}/commit
```

`install.py` 做了什么：
- 生成 `/opt/unitao/wg_agent.conf` — 指定 networkConfigPath、inventoryTool、wgDir
- 创建 systemd unit `wg-agent.service` 并 **enable**
- 默认 networkConfigPath 指向 `/opt/unitao/wireguard_network.json`
- 不复制脚本、不创建 WG 目录 — 这些由部署流程自行处理

### 2. VM 启动

`install.py` 已 enable systemd 服务，VM 启动后 systemd 自动执行：

```
systemd → wg_agent.py
         → 读取 /opt/unitao/wg_agent.conf → 获取 inventoryTool、networkConfigPath、wgDir
         → 从 networkConfigPath 加载 WgNetworkConfig → 获取 networkName
         → 密钥文件不存在? (prep_image_for_commit.py 已清除)
         → 生成新密钥对 (每台 VM 身份唯一)
         → 回填 publicKey 到 wireguard_network.json
         → 轮询 inventory_tool.py --get wireguard_network_inv.json
         → timestamp 变更 → 合并 selfIp + peers → 保存
         → 生成 wg.conf → wg-quick up
```

手动管理 (调试用)：

```bash
# 手动运行
python3 /opt/unitao/wg_agent.py

# 服务管理
python3 wg_agent_service.py install     # 安装 + enable + start
python3 wg_agent_service.py status      # 查看状态
python3 wg_agent_service.py uninstall   # 停止 + 禁用 + 删除
```

### 3. inventory_tool.py

`shareInventoryData=True` 时 cloud-init 自动部署 `/opt/unitao-server-config/inventory_tool.py`。
`wg_agent.py` 通过调用该脚本从 Host API **只读** 拉取 `wireguard_network_inv.json`，不 POST 数据。

`inventory_tool.py` 自动读取同目录下的 `inventory.json`（含 hostApiUrl 和 vmId），因此 VM 无需额外配置。`wg_agent.conf` 中的 `inventoryTool` 指向此脚本路径。

## 前置依赖

- **VM 侧**: Python 3, `wireguard-tools` (wg, wg-quick), `iproute2` (ip)
- **Host 侧**: REST API (已有)
- **外部**: Orchestrator (本工具范围外)

## 相关计划

详见 `plan/domain-wireguard/plan.md`
