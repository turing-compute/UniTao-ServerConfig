# WireGuard Image 制作流程

## 概述

制作一个预装 WireGuard Agent 的 Ubuntu 26.04 base image。克隆出的 VM 启动后自动生成密钥、发布 `wireguard_network.json` 到 Host inventory。

### 最终效果

- VM boot → wg-agent 自动启动
- 生成 `primary.pem` / `primary.pub.pem` 密钥对
- 创建 `wireguard_network.json` 并上传到 Host inventory
- 等待 Orchestrator 回填 `assigned_ip` 后自动拉起 `wg0`

---

## 前置条件

- KVM Host 已部署 REST API
- 已有 Ubuntu 26.04 基础 image（如 `ubuntu26.04`）
- 本地有 WireGuard domain tool 源码

---

## 步骤

### 1. 创建 Image

基于现有 Ubuntu 26.04 base image 创建一个新的 WireGuard image：

```bash
HOST="http://<host_ip>:5000"
IMAGE_NAME="wireguard26.04.01"

# 创建 image（基于 ubuntu-26.04 base）
curl -s -X POST $HOST/api/v1/images/$IMAGE_NAME \
  -H "Content-Type: application/json" -d '{
    "imageFormat": "qcow2",
    "imageSource": "local",
    "baseImagePath": "../ubuntu-26.04.qcow2",
    "baseImageFormat": "qcow2"
  }'

# 等待 image ready
curl -s $HOST/api/v1/images/$IMAGE_NAME | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['downloadState'])"
# 应输出: ready
```

> 如果 Host 上已有 `wireguard26.04.01` image，想重新开始：
> ```bash
> curl -s -X DELETE $HOST/api/v1/images/$IMAGE_NAME
> ```
>
> Image 名可以自定，后续 `osImage` 参数对应即可。

### 2. 创建 VM

用 Host REST API 创建一台用于 image 准备的 VM：

```bash
HOST="http://<host_ip>:5000"

# 创建 VM（DHCP + HostKey 认证）
curl -s -X POST $HOST/api/v1/vms -H "Content-Type: application/json" -d '{
  "id": "wireguard-prep",
  "cpu": 2,
  "ramInGB": 2,
  "vmHostName": "wireguard-prep",
  "osImage": "wireguard26.04.01",
  "osVariant": "ubuntu24.04",
  "bridge": "ovs-br0",
  "useDHCP4": true,
  "diskSizeGB": 20,
  "authType": "HostKey",
  "shareInventoryData": true,
  "prepareDomainImage": true
}'

# 启动
curl -s -X POST $HOST/api/v1/vms/wireguard-prep/start
```

等 cloud-init 完成（inventory 中出现 `network-info.json`）：

```bash
curl -s $HOST/api/v1/vms/wireguard-prep/inventory | python3 -m json.tool
```

### 3. 部署 Agent

先获取 VM 的 IP（DHCP 分配）：

```bash
curl -s $HOST/api/v1/vms/wireguard-prep/inventory/network-info.json | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['content']['data']['interfaces'][0]['ip'])"
```

然后用 `deploy.ps1`（PowerShell）或 `deploy.sh`（Git Bash）部署：

**PowerShell:**
```powershell
cd src/domain/wireguard
.\deploy.ps1 <vm_ip>
```

**Git Bash:**
```bash
cd src/domain/wireguard
./deploy.sh <vm_ip>
```

部署脚本会自动：
- 复制所有 Python 文件到 `/opt/unitao/domain/wireguard/`
- 升级系统包（`apt upgrade` + `dist-upgrade`），需要重启则自动重启
- 安装 `wireguard-tools`
- 创建 `wg_agent.conf`
- 安装并 enable `wg-agent.service`
- 启动 `wg-agent`

### 4. 验证 Agent 工作正常

```bash
ssh ubuntu@<vm_ip>

# 检查服务
sudo systemctl status wg-agent

# 确认 wireguard_network.json 已上传到 inventory
# 在 Host 上：
curl -s $HOST/api/v1/vms/wireguard-prep/inventory | python3 -m json.tool
# 应该看到 wireguard_network.json
```

用 Orchestrator 回填测试一下完整流程：

```bash
# Post backfill
curl -s -X POST $HOST/api/v1/vms/wireguard-prep/inventory \
  -H "Content-Type: application/json" -d '{
  "name": "wireguard_network",
  "data": {
    "public_keys": {"primary": "SHOULD_NOT_CHANGE"},
    "network": {
      "assigned_ip": "10.200.0.1/32",
      "listen_port": 51820,
      "dns_servers": ["8.8.8.8"],
      "peers": []
    }
  }
}'

# 等 30s 后检查 wg0
ssh ubuntu@<vm_ip> "systemctl is-active wg-quick@wg0 && sudo wg show wg0"
```

### 5. 运行 prep 清理

确认 Agent 工作正常后，运行两个 prep 脚本（prep 会自动 stop 服务）：

```bash
ssh ubuntu@<vm_ip>

# WireGuard 级清理（自动停止服务、删除密钥、wg0.conf、wireguard_network.json，保留 wg_agent.conf）
sudo python3 /opt/unitao/domain/wireguard/prep_image_for_commit.py --force

# VM 级清理（删除 SSH host keys、machine-id、cloud-init 状态等）
sudo python3 /opt/unitao-server-config/prep_image_for_commit.py --force

# 确认 wg_agent.conf 保留、wireguard_network.json 已删除
ls -la /opt/unitao/wg_agent.conf
ls -la /opt/unitao/wireguard_network.json   # 应该不存在

# 关机
sudo shutdown -h now
```

> **prep_image_for_commit.py 清理清单：**
> - `/etc/wireguard/wg0.conf` — 生成的 WG 配置
> - `/etc/wireguard/wg0/` — 密钥文件（primary.pem, primary.pub.pem）
> - `/opt/unitao/wireguard_network.json` — Agent 生成的网络配置
> - `/opt/unitao/wireguard_network_inv.json` — 下载的 inv 文件
> - **保留** `/opt/unitao/wg_agent.conf` — install.py 生成的路径配置

### 6. Commit Image

等 VM 关机后在 Host 上 commit：

```bash
# 确认 VM 已关机
curl -s $HOST/api/v1/vms/wireguard-prep | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['virshState'])"
# 应输出: notExists

# 停止（确保 vmState=stopped 才能 commit）
curl -s -X POST $HOST/api/v1/vms/wireguard-prep/stop

# Commit: 把 qcow2 变更写入 backing image
curl -s -X POST $HOST/api/v1/vms/wireguard-prep/commit -H "Content-Type: application/json" -d '{}'
```

如果 commit 的目标 image 名不同，可以改 VM 的 disk 配置里的 `baseImagePath`。

### 7. 验证 Image

删除 prep VM，用新 image 重建测试：

```bash
# 删除
curl -s -X DELETE $HOST/api/v1/vms/wireguard-prep

# 重建（用 DHCP 避免 IP 冲突）
curl -s -X POST $HOST/api/v1/vms -H "Content-Type: application/json" -d '{
  "id": "wireguard-test",
  "cpu": 2,
  "ramInGB": 2,
  "vmHostName": "wireguard-test",
  "osImage": "wireguard26.04.01",
  "osVariant": "ubuntu24.04",
  "bridge": "ovs-br0",
  "useDHCP4": true,
  "shareInventoryData": true,
  "prepareDomainImage": true
}'

curl -s -X POST $HOST/api/v1/vms/wireguard-test/start
```

验证新 VM：

```bash
# 等 10-20s，检查 inventory
curl -s $HOST/api/v1/vms/wireguard-test/inventory | python3 -m json.tool
# 应该看到: network-info.json, wireguard_network.json

# 查 IP 并 SSH
IP=$(curl -s $HOST/api/v1/vms/wireguard-test/inventory/network-info.json | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['content']['data']['interfaces'][0]['ip'])")
ssh ubuntu@$IP "systemctl is-active wg-agent"
# 应输出: active

# 此时 wg-quick@wg0 应该是 inactive（等 Orchestrator 回填）
ssh ubuntu@$IP "systemctl is-active wg-quick@wg0"
# 应输出: inactive
```

### 8. Orchestrator 回填

```bash
curl -s -X POST $HOST/api/v1/vms/wireguard-test/inventory \
  -H "Content-Type: application/json" -d '{
  "name": "wireguard_network",
  "data": {
    "public_keys": {"primary": "SHOULD_NOT_CHANGE"},
    "network": {
      "assigned_ip": "10.200.0.1/32",
      "listen_port": 51820,
      "dns_servers": ["8.8.8.8"],
      "peers": []
    }
  }
}'

# 等 30s（agent 轮询间隔），检查
ssh ubuntu@$IP "systemctl is-active wg-quick@wg0 && sudo wg show wg0"
# 应该 active 且 wg0 UP
```

---

## 文件结构（Image 中）

```
/opt/unitao/
├── wg_agent.conf                         # Agent 路径配置（install.py 生成，prep 保留）
├── wireguard_network.json                # Agent 首运行生成（prep 删除）
├── wireguard_network_inv.json            # 从 inventory 下载（prep 删除）
└── domain/
    ├── __init__.py
    └── wireguard/
        ├── __init__.py
        ├── wg_data.py                    # 数据模型
        ├── wg_agent.py                   # Agent 主程序
        ├── wg_config_builder.py          # wg.conf 构建
        ├── wg_key_manager.py             # 密钥管理（primary.pem / primary.pub.pem）
        ├── wg_agent_service.py           # systemd 服务管理
        ├── install.py                    # 安装脚本
        └── prep_image_for_commit.py      # Image 清理脚本

/etc/systemd/system/
└── wg-agent.service                      # systemd unit（install.py 生成，WantedBy=multi-user.target）

/etc/wireguard/
└── wg0/                                  # 密钥目录（prep 删除，Agent 首运行重建）
    ├── primary.pem                       # 私钥
    └── primary.pub.pem                   # 公钥
```

---

## 数据流

```
Agent 启动:
  1. 加载 wg_agent.conf → 获取路径
  2. 密钥不存在 → wg genkey → 写入 primary.pem / primary.pub.pem
  3. wireguard_network.json 不存在 → 创建（含 public_key）
  4. 上传到 inventory（失败则每 5s 重试直到成功）
  5. 死循环:
     a. 下载 inventory 的 wireguard_network.json
     b. 比较 API "Last modified" timestamp
     c. 变更且内容不同 → 合并 data.network 到本地
     d. assigned_ip 存在 → systemctl start wg-quick@wg0
     e. sleep 30s
```
