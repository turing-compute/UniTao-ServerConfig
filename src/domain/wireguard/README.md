# WireGuard 领域工具

VM Mesh VPN 的 WireGuard 配置管理。所有 peer 对等，Orchestrator 统一规划拓扑和 IP 分配。

## 架构

```
┌──────────────────────────────────────────────────┐
│ Orchestrator (外部)                               │
│   ① 读取各 VM 公钥                                │
│   ② 决定拓扑 + 分配 IP                            │
│   ③ 一次性回填所有 self + peers                   │
└────────────────────┬─────────────────────────────┘
                     │ Host REST API (通用，已有)
                     ▼
┌──────────────────────────────────────────────────┐
│ Host Inventory                                    │
│   vm-*/inventory/wireguard_config.json            │
└────────────────────┬─────────────────────────────┘
                     │ VM 轮询
                     ▼
┌──────────────────────────────────────────────────┐
│ VM: wg_agent.py                                   │
│   ① 生成密钥 → ② 发布公钥 → ③ 轮询配置            │
│   → ④ 生成 wg.conf → ⑤ wg-quick up               │
└──────────────────────────────────────────────────┘
```

## 文件说明

| 文件 | 运行位置 | 用途 |
|------|---------|------|
| `wg_data.py` | 任意 | IP 工具函数 + WgNetworkConfig + WgPeerData 数据模型 |
| `wg_config_file.py` | 任意 | `wireguard_config.json` 的读写和校验 |
| `wg_key_manager.py` | VM | 密钥对生成与管理 (wg genkey/pubkey) |
| `wg_config_builder.py` | 任意 | 将数据模型构建为 `wg.conf` 文本 |
| `wg_agent.py` | **VM** | Agent 主程序：发布公钥、轮询、激活接口 |
| `wg_agent_service.py` | **VM** | systemd 服务安装/卸载/状态检查 |
| `install.py` | image 构建时 | 将 agent 安装到 VM image 中 |
| `prep_image_for_commit.py` | image 构建时 | commit 前清除 WireGuard 密钥和运行时文件 |

## 数据流

```
第一阶段: VM Agent 通过 inventory_tool.py 发布公钥
  inventory_tool.py --data /tmp/xxx.json
  {
    "public_keys": {"primary": "..."},
    "network": {
      "listen_port": 51820,
      "dns_servers": ["8.8.8.8"],
      "peers": []
    }
  }

第二阶段: Orchestrator 回填所有配置
  {
    "public_keys": {"primary": "..."},
    "network": {
      "assigned_id": "10.200.0.1",
      "listen_port": 51820,
      "dns_servers": ["8.8.8.8"],
      "peers": [
        {"public_key": "...", "endpoint": "192.168.122.11:51820",
         "assigned_id": "10.200.0.2", "allowed_ips": ["10.200.0.2/32"]}
      ]
    }
  }

第三阶段: VM Agent 检测到 network.assigned_id 非空 → 生成 wg.conf → wg-quick up
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
- 不复制脚本、不创建 WG 目录 — 这些由 `wg_agent.py` 自行处理

### 2. VM 启动

`install.py` 已 enable systemd 服务，VM 启动后 systemd 自动执行：

```
systemd → wg_agent.py
         → 读取 /opt/unitao/wg_agent.conf → 获取 inventoryTool、networkConfigPath、wgDir
         → 从 networkConfigPath 加载 WgNetworkConfig → 获取 networkName
         → 密钥文件不存在? (prep_image_for_commit.py 已清除)
         → 生成新密钥对 (每台 VM 身份唯一)
         → 通过 inventory_tool.py 发布 wireguard_config.json:
           {"public_keys": {"primary": "..."}, "network": {"listen_port": ..., "peers": []}}
         → 轮询 inventory_tool.py --get wireguard_config.json
         → detected network.assigned_id 非空 + peers 齐备
         → 生成 wg.conf → wg-quick up
```

手动管理 (调试用)：

```bash
# 手动运行
python3 /opt/unitao/wg_agent.py 

# 仅发布公钥，不轮询
python3 /opt/unitao/wg_agent.py --publish-only

# 检查并应用一次后退出
python3 /opt/unitao/wg_agent.py --once

# 服务管理
python3 wg_agent_service.py install     # 安装 + enable + start
python3 wg_agent_service.py status                         # 查看状态
python3 wg_agent_service.py uninstall                      # 停止 + 禁用 + 删除
```

### 3. 网络配置文件

VM 上需要放置一个网络配置文件 (由 `install.py` 复制或 cloud-init 注入)：

```json
{
  "networkName": "wg-mesh",
  "subnet": "10.200.0.0/24",
  "listenPort": 51820,
  "dnsServers": ["10.200.0.1"],
  "mtu": 1420,
  "persistentKeepalive": 25,
  "routes": [
    {"destination": "10.200.0.0/24", "description": "VPN 子网"}
  ]
}
```

### 4. inventory_tool.py

`shareInventoryData=True` 时 cloud-init 自动部署 `/opt/unitao-server-config/inventory_tool.py`。
`wg_agent.py` 通过调用该脚本与 Host API 交互（POST/GET inventory），无需直接处理 HTTP。

`inventory_tool.py` 自动读取同目录下的 `inventory.json`（含 hostApiUrl 和 vmId），因此 VM 无需额外配置。`wg_agent.conf` 中的 `inventoryTool` 指向此脚本路径。

## 前置依赖

- **VM 侧**: Python 3, `wireguard-tools` (wg, wg-quick), `iproute2` (ip)
- **Host 侧**: REST API (已有, 步骤 1–2)
- **外部**: Orchestrator (本工具范围外)

## 相关计划

详见 `plan/domain-wireguard/plan.md`
