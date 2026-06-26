# Netmaker Server Image 准备指南

本文档记录在 KVM 宿主机上准备 Netmaker WireGuard 管理平台基础镜像的完整流程。

## 背景

Netmaker 是一个开源的 WireGuard 管理平台，提供 Web UI 和 API 来管理 VPN 网络、节点和访问控制。

本镜像的目的：创建一个可复用的 Netmaker server 基础镜像（`Netmaker26.04`），基于此镜像创建的 VM 可直接作为 WireGuard VPN 管理服务器使用。

## 架构

```
KVM Host (192.168.2.101)
  └── Netmaker26.04 image
       ├── 基于 ubuntu-26.04 cloud image
       ├── Netmaker v1.6.0 (直接安装，非 Docker)
       ├── Mosquitto MQTT Broker
       ├── Nginx + 简易管理面板 (端口 80)
       └── 首次启动自动初始化 (随机凭据)

首次启动后：
  VM 客户端 ──netclient join──▶ Netmaker Server ──WireGuard──▶ 其他节点
```

## 准备流程

### 前置条件

- KVM 宿主机已部署 UniTao REST Agent（`http://192.168.2.101:5000`）
- 已有 `ubuntu-26.04` 基础镜像（从 Ubuntu cloud images 下载）
- 桥接网络 `ovs-br0` 可用

### 步骤 1：创建基础镜像

```bash
# 基于 ubuntu-26.04 创建 Netmaker26.04 镜像 (本地 qcow2)
curl -X POST http://192.168.2.101:5000/api/v1/images/Netmaker26.04 \
  -H "Content-Type: application/json" \
  -d '{
    "imageFormat": "qcow2",
    "imageSource": "local",
    "baseImagePath": "../ubuntu-26.04.qcow2",
    "baseImageFormat": "qcow2"
  }'

# 等待 downloadState 变为 "ready"
curl http://192.168.2.101:5000/api/v1/images/Netmaker26.04
```

### 步骤 2：创建准备 VM

```bash
curl -X POST http://192.168.2.101:5000/api/v1/vms \
  -H "Content-Type: application/json" \
  -d '{
    "id": "prep_netmaker",
    "cpu": 2,
    "ramInGB": 2,
    "vmHostName": "prep-netmaker",
    "osImage": "Netmaker26.04",
    "osVariant": "generic",
    "bridge": "ovs-br0",
    "authType": "HostKey",
    "shareInventoryData": true,
    "prepareDomainImage": true,
    "diskSizeGB": 30
  }'

# 启动 VM
curl -X POST http://192.168.2.101:5000/api/v1/vms/prep_netmaker/start

# 获取 IP
curl http://192.168.2.101:5000/api/v1/vms/prep_netmaker/inventory/network-info.json
```

### 步骤 3：基础环境配置

SSH 进入 VM，执行：

```bash
# 修改 apt 源为阿里云镜像（Ubuntu 26.04 使用 DEB822 格式）
sudo sed -i '
  s|http://archive.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g
  s|http://security.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g
' /etc/apt/sources.list.d/ubuntu.sources

sudo apt update
sudo apt upgrade -y

# 如果升级了内核，重启
sudo reboot
```

### 步骤 4：安装 Netmaker

```bash
# 安装依赖
sudo apt install -y mosquitto wireguard-tools curl nginx

# 下载 Netmaker v1.6.0 (注意：中国大陆需使用 GitHub 代理)
# 实际使用 https://ghfast.top/ 代理
sudo curl -L -o /usr/local/bin/netmaker \
  "https://ghfast.top/https://github.com/gravitl/netmaker/releases/download/v1.6.0/netmaker-linux-amd64"
sudo chmod +x /usr/local/bin/netmaker

# 下载 nmctl 管理工具
sudo curl -L -o /usr/local/bin/nmctl \
  "https://ghfast.top/https://github.com/gravitl/netmaker/releases/download/v1.6.0/nmctl-linux-amd64"
sudo chmod +x /usr/local/bin/nmctl
```

### 步骤 5：配置 Mosquitto MQTT

```bash
sudo tee /etc/mosquitto/conf.d/netmaker.conf <<EOF
listener 1883 0.0.0.0
allow_anonymous true
EOF

sudo systemctl restart mosquitto
sudo systemctl enable mosquitto
```

### 步骤 6：Netmaker 配置文件

```bash
MK=$(python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())")

sudo mkdir -p /etc/netmaker
sudo tee /etc/netmaker/config.json <<CONF
{
  "server": {
    "host": "localhost",
    "apiport": "8081",
    "grpcport": "50051",
    "grpchost": "localhost",
    "coreDNSAddr": "localhost",
    "masterkey": "$MK",
    "mqhost": "localhost",
    "mqport": "1883",
    "restbackend": true,
    "verbosity": 2,
    "database": "sqlite"
  }
}
CONF
```

### 步骤 7：关键修复 — MQTT Broker 连接

**问题**：Netmaker v1.6.0 会自动探测宿主机的公网 IP（如 `114.248.89.153`），并将 MQTT broker 地址解析为该公网 IP，导致内网环境无法连接。

**解决**：使用 iptables DNAT 规则将发往公网 IP 的 MQTT 流量重定向到本地。

```bash
# 添加 iptables 重定向规则
sudo iptables -t nat -A OUTPUT -d <公网IP> -p tcp --dport 1883 \
  -j DNAT --to-destination 127.0.0.1:1883

# 持久化规则
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

> **注意**：需要先确定宿主机的公网 IP（可用 `curl ifconfig.me` 查看），然后在配置文件中将 `host` 等字段设为实际的 LAN IP（如 `192.168.2.29`），否则 enrollment token 中会编码错误的 server 地址。

### 步骤 8：Systemd 服务

```bash
sudo tee /etc/systemd/system/netmaker.service <<SVC
[Unit]
Description=Netmaker Server
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
ExecStart=/usr/local/bin/netmaker -c /etc/netmaker/config.json
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC

sudo systemctl daemon-reload
sudo systemctl enable netmaker
sudo systemctl start netmaker
```

### 步骤 9：Nginx + 管理面板

```bash
# 创建简易管理面板 (由于 netmaker-ui Docker 镜像无法下载)
sudo mkdir -p /var/www/html
# 编写 index.html (见下方附录)

sudo tee /etc/nginx/sites-available/default <<NGINX
server {
    listen 80;
    root /var/www/html;
    index index.html;
    location / {
        try_files \$uri \$uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINX

sudo nginx -t
sudo systemctl restart nginx
```

### 步骤 10：创建管理员用户

Netmaker v1.6.0 的 REST API 无法直接创建用户（返回 `record not found`），需通过 SQLite 直接插入：

```bash
sudo apt install -y sqlite3 python3-bcrypt

MK="<你的MasterKey>"
HASH=$(python3 -c "
import bcrypt
print(bcrypt.hashpw(b'admin-password', bcrypt.gensalt()).decode())
")
UID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")

sudo sqlite3 /data/netmaker.db \
  "INSERT INTO users_v1 (id, username, password, platform_role_id, created_at, updated_at)
   VALUES ('$UID', 'admin', '$HASH', 'super-admin', datetime('now','utc'), datetime('now','utc'));"
```

### 步骤 11：首次启动自动初始化

初始化脚本文件在 `src/domain/netmaker-wireguard/netmaker-init.sh`，通过 SCP 上传到 VM：

```bash
# 从本地仓库上传脚本到 VM
scp src/domain/netmaker-wireguard/netmaker-init.sh ubuntu@<vm-ip>:/tmp/

# 在 VM 上安装
ssh ubuntu@<vm-ip>
sudo cp /tmp/netmaker-init.sh /usr/local/sbin/netmaker-init
sudo chmod +x /usr/local/sbin/netmaker-init

# 创建 systemd 服务
sudo tee /etc/systemd/system/netmaker-init.service <<SVC
[Unit]
Description=Netmaker First-Boot Init
After=netmaker.service mosquitto.service
Requires=netmaker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/netmaker-init
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
SVC

sudo systemctl enable netmaker-init
```

### 步骤 12：清理并提交

```bash
# 清理数据（移除当前 MasterKey 和数据库）
sudo rm -f /data/netmaker.db /etc/netmaker/.initialized /etc/netmaker/credentials.txt

# 替换为占位符
sudo tee /etc/netmaker/config.json <<CONF
{"server":{"host":"localhost","apiport":"8081","grpcport":"50051","grpchost":"localhost","coreDNSAddr":"localhost","masterkey":"REPLACE_ME_ON_FIRST_BOOT","mqhost":"localhost","mqport":"1883","restbackend":true,"verbosity":2,"database":"sqlite"}}
CONF

# 持久化 iptables 规则
sudo netfilter-persistent save

# 执行 VM 级清理
sudo python3 /opt/unitao-server-config/prep_image_for_commit.py --force
sudo poweroff
```

在宿主机上：
```bash
# Commit
curl -X POST http://192.168.2.101:5000/api/v1/vms/prep_netmaker/stop
curl -X POST http://192.168.2.101:5000/api/v1/vms/prep_netmaker/commit \
  -H "Content-Type: application/json" -d '{"disk":0}'

# 清理
curl -X DELETE http://192.168.2.101:5000/api/v1/vms/prep_netmaker
```

## 遇到的问题及解决

### 1. Ubuntu 26.04 apt 源配置
- **问题**：Ubuntu 26.04 使用 DEB822 格式（`.sources` 文件），而非传统的 `sources.list`
- **解决**：修改 `/etc/apt/sources.list.d/ubuntu.sources` 中的 URIs

### 2. 磁盘空间不足
- **问题**：默认镜像只有 ~4GB，内核升级包（~320MB）导致空间不足
- **解决**：创建 VM 时指定 `diskSizeGB: 30`

### 3. GitHub 下载超时
- **问题**：从中国大陆访问 GitHub 下载 30MB 的 netmaker 二进制文件非常慢
- **解决**：使用 `https://ghfast.top/` 代理加速

### 4. Netmaker MQTT Broker 连接
- **问题**：v1.6.0 自动探测公网 IP，MQTT 连接指向公网地址
- **解决**：iptables DNAT 规则重定向 + 持久化

### 5. 用户创建失败
- **问题**：`POST /api/users/<name>` 始终返回 `record not found`
- **解决**：通过 SQLite 直接插入 `users_v1` 表（注意表名是 `users_v1` 不是 `users`）

### 6. netmaker-ui 无法下载
- **问题**：官方 UI Docker 镜像和 GitHub release 均无法从中国大陆访问
- **解决**：创建简易 HTML 管理面板，通过 nginx 代理 API

### 7. enrollment token 编码错误的 server 地址
- **问题**：token 中 server 地址为公网 IP 而非 LAN IP
- **解决**：将配置中 `host`、`grpchost` 等字段设为 LAN IP

## 使用已创建的 Image

```bash
# 1. 基于 Netmaker26.04 创建 VM
curl -X POST http://192.168.2.101:5000/api/v1/vms \
  -H "Content-Type: application/json" \
  -d '{
    "id": "netmaker-01",
    "cpu": 2,
    "ramInGB": 2,
    "vmHostName": "netmaker-01",
    "osImage": "Netmaker26.04",
    "osVariant": "generic",
    "bridge": "ovs-br0",
    "authType": "HostKey",
    "shareInventoryData": true,
    "diskSizeGB": 30
  }'

# 2. 启动并获取 IP
# 3. SSH 登录，查看凭据
cat /etc/netmaker/credentials.txt

# 4. 打开管理面板 http://<vm-ip>

# 5. 创建 VPN 网络
nmctl context set local --endpoint http://localhost:8081 --master_key <MasterKey>
nmctl context use local
nmctl network create --name my-net --ipv4_addr "10.200.0.0/24"

# 6. 生成 enrollment key
nmctl enrollment_key create --networks my-net --unlimited --tags "default"

# 7. 客户端加入
# 在客户端 VM 上安装 netclient，执行：
netclient join -t <token>
```

## 服务端口

| 端口 | 服务 | 说明 |
|------|------|------|
| 80 | Nginx | 管理面板 + API 代理 |
| 1883 | Mosquitto | MQTT Broker（仅 localhost） |
| 8081 | Netmaker | REST API |
| 50051 | Netmaker | gRPC |

## 注意事项

- 如果宿主机公网 IP 变化，需更新 iptables 规则中的目标 IP
- 管理面板是简易版，完整的 Netmaker UI 需要另外部署
- 首次启动约需 30 秒初始化（netmaker-init 等待 broker 连接）
- 凭据文件 `/etc/netmaker/credentials.txt` 权限为 600，仅 root 可读
