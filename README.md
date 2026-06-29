# UniTao-ServerConfig

数据驱动的服务器配置自动化工具。将**配置数据**（JSON 文件定义期望状态）与**执行逻辑**（Python 脚本调和系统状态）分离 — 类似 Terraform 的期望状态调和模式。

## 快速开始

```bash
# 安装 Python 依赖
./src/req_install.sh

# 安装 KVM 系统包
./src/kvm_install.sh

# 运行组件
./src/runpy.sh src/kvm/vm/kvm_vm.py --path <vm.json>
./src/runpy.sh src/kvm/image/kvm_image.py --path <image.json>
./src/runpy.sh src/network/bridge/net_bridge.py --path <bridge.json>

# 启动 REST API（在 KVM Host 上）
./src/rest/restapp.sh
```

## 模块

### KVM 虚拟化 (`src/kvm/`)

创建和管理 KVM 虚拟机、磁盘镜像、网络接口。

- **VM** (`kvm_vm.py`) — 虚拟机生命周期（创建、启动、停止、删除），Cloud-Init ISO 生成，支持多种认证方式
- **Image** (`kvm_image.py`) — qcow2 镜像创建，支持远程下载和本地 backing file 模式
- **Network** — 网桥接口配置，支持 Linux Bridge 和 OVS，静态 IP / DHCP

### REST API (`src/rest/`)

Flask Agent 部署在每台 KVM Host 上，通过 HTTP API 管理资源。

| 端点 | 说明 |
|------|------|
| `GET/POST /api/v1/vms` | 列出/创建 VM |
| `GET/DELETE /api/v1/vms/<name>` | 获取/删除 VM |
| `POST /api/v1/vms/<name>/start` | 启动 VM |
| `POST /api/v1/vms/<name>/stop` | 停止 VM |
| `POST /api/v1/vms/<name>/commit` | 提交磁盘变更到 base image |
| `POST /api/v1/vms/<name>/inventory` | VM 数据上报 |
| `GET/PATCH /api/v1/vms/<name>/inventory/<file>` | 读取/更新 inventory |
| `GET/POST /api/v1/images` | 列出/创建镜像 |
| `GET/POST /api/v1/bridges` | 列出/创建网桥 |
| `GET /api/v1/utils/health` | 健康检查 |
| `GET /api/v1/utils/mac` | 随机 MAC 地址 |

### WireGuard 领域工具 (`src/domain/wireguard/`)

VM 之间组建 WireGuard VPN 网络。Host 是纯数据平台，不包含 WireGuard 领域知识。

- **`wg_agent.py`** — VM 侧 Agent，作为 systemd service 运行：生成密钥对、发布公钥、轮询 Orchestrator 下发的配置、生成 wg.conf 并激活接口
- **`wg_data.py`** — 数据模型：`WgNetworkConfig`（网络配置校验）、`WgPeerData`（Peer 信息校验）、IP 工具函数
- **`wg_config_builder.py`** — 将数据模型构建为 `wg.conf` 文本
- **`wg_key_manager.py`** — VM 侧密钥对生成（`wg genkey` / `wg pubkey`）
- **`wg_agent_service.py`** — systemd unit 安装与管理

[详细设计文档](plan/domain-wireguard/plan.md)

### 安全 (`src/security/`)

- **`key_manager.py`** — RSA 4096 密钥管理，支持加密/解密（RSA-OAEP-SHA256）
- **`password_gen.py`** — 密码学安全随机密码生成
- **`generate_keys.py`** — 部署时密钥对生成脚本
- **`inventory_tool.py`** — VM 侧工具，与 Host REST API 交换数据（stdlib only）

VM 认证方式（`POST /api/v1/vms` 时通过 `authType` 字段声明）：

| authType | 密码 | SSH 密钥 | 说明 |
|----------|:---:|:---:|------|
| `CustomerPWD` | 客户指定 | — | 客户提供明文密码，密码登录 |
| `RandomPWD` | 随机生成 | — | 自动生成密码学安全随机密码 |
| `HostKey` | 无 | Host 公钥 | Host RSA 密钥对免密登录，关闭密码 |
| `CustomerKey` | 无 | 客户公钥 | 客户提供 SSH 公钥列表 |
| `NoAuth` | 无 | 无 | 无认证，全自动 VM |
| 未声明 | 无 | 无 | 不做任何密码/密钥设置 |

[详细设计文档](plan/vm-secure-access/plan.md)

### 网桥 (`src/network/bridge/`)

Linux Bridge 和 OVS Bridge 的数据模型与验证。

## 架构

```
JSON 数据文件 → Python 校验 → 系统命令调和
```

- **基于文件的实体标识**：JSON 文件名即实体名称
- **`Util.run_command()`**：subprocess 封装，非零退出则抛异常
- **`{vmPath}` 占位符**：支持相对于 VM 目录的路径引用
- **无外部配置框架**：直接调用 `virsh`、`qemu-img`、`wg`、`ip` 等系统命令

## 项目结构

```
src/
├── shared/              # 共享库 (utilities, logger)
├── kvm/
│   ├── vm/kvm_vm.py     # VM 管理
│   ├── image/           # 镜像管理
│   └── .../             # 磁盘、网络子模块
├── rest/                # Flask REST API
│   ├── app.py           # 应用工厂
│   ├── service.py       # JSON 持久化层
│   ├── api_vm.py        # VM Blueprint
│   ├── api_image.py     # Image Blueprint
│   ├── api_bridge.py    # Bridge Blueprint
│   └── api_utils.py     # 工具 Blueprint
├── security/            # 密钥管理、密码生成
├── domain/wireguard/    # WireGuard Agent
├── network/bridge/      # 网桥数据模型
└── Archive/             # 早期迭代/遗留代码
```
