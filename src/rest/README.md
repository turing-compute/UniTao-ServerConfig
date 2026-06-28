# UniTao KVM Host REST Agent

## 安装依赖

```bash
./src/req_install.sh
```

## 手动启动

```bash
./src/runpy.sh src/rest/app.py --config /etc/unitiao/config.json
```

默认 `--config` 为 `/etc/unitiao/config.json`。

## 部署为系统服务

```bash
sudo ./service/deploy-service.sh /etc/unitiao
```

## API 通用格式

请求和响应均使用 JSON。成功响应:

```json
{"success": true, "data": {...}}
```

错误响应:

```json
{"success": false, "error": {"code": "ERROR_CODE", "message": "..."}}
```

---

## Image 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/images` | 列出所有 image |
| GET | `/api/v1/images/<name>` | 查看 image 详情（downloadState） |
| POST | `/api/v1/images/<name>` | 创建 image（基于 base image） |
| DELETE | `/api/v1/images/<name>` | 删除 image |

### 创建 Image

```json
POST /api/v1/images/<name>
{
  "imageFormat": "qcow2",
  "imageSource": "local",
  "baseImagePath": "../ubuntu-26.04.qcow2",
  "baseImageFormat": "qcow2"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| imageFormat | string | 镜像格式，通常 `qcow2` |
| imageSource | string | `"local"` 基于已有镜像；`"remote"` 从 downloadLink 下载 |
| baseImagePath | string | 基镜像路径（相对于 `/opt/kvm/images/`） |
| baseImageFormat | string | 基镜像格式 |

等待 `downloadState` 变为 `"ready"` 后即可在 VM 创建时引用。

---

## VM 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/vms` | 列出所有 VM |
| POST | `/api/v1/vms` | 创建/更新 VM |
| GET | `/api/v1/vms/<name>` | 查看 VM 详情（virsh state、disk/net JSON、inventory） |
| DELETE | `/api/v1/vms/<name>` | 删除 VM |
| POST | `/api/v1/vms/<name>/start` | 启动 VM |
| POST | `/api/v1/vms/<name>/stop` | 停止 VM |
| POST | `/api/v1/vms/<name>/commit` | 将 qcow2 disk 变更提交到 backing image |

### 创建 VM

```json
POST /api/v1/vms
{
  "id": "my-vm",
  "cpu": 2,
  "ramInGB": 2,
  "vmHostName": "my-vm",
  "osImage": "ubuntu26.04",
  "osVariant": "ubuntu24.04",
  "bridge": "ovs-br0",
  "useDHCP4": true,
  "shareInventoryData": true,
  "prepareDomainImage": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | VM 唯一标识 |
| cpu | int | 是 | vCPU 数量 |
| ramInGB | int | 是 | 内存（GB） |
| vmHostName | string | 是 | VM hostname |
| osImage | string | 是 | Image 名称 |
| osVariant | string | 是 | OS 类型（如 `ubuntu24.04`） |
| bridge | string | 是 | 网桥名称 |
| useDHCP4 | bool | 否 | 使用 DHCP（默认 false，需同时提供 ipv4 + gateway4） |
| ipv4 | string | 否 | 静态 IP（CIDR 格式，如 `192.168.1.100/24`） |
| gateway4 | string | 否 | 网关地址 |
| shareInventoryData | bool | 否 | 注入 inventory_tool.py 和 report_network.py |
| prepareDomainImage | bool | 否 | 注入 prep_image_for_commit.py |
| diskSizeGB | int | 否 | 磁盘大小（GB），默认 10 |

### Commit Image

```json
POST /api/v1/vms/<name>/commit
{}
```

要求 VM 已停止且 virsh state 为 `notExists` 或 `shutOff`。

---

## Inventory 管理

用于 VM ↔ Host 之间交换数据文件。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/vms/<name>/inventory` | 列出 inventory 文件 |
| GET | `/api/v1/vms/<name>/inventory/<file>` | 获取文件内容和时间戳 |
| POST | `/api/v1/vms/<name>/inventory` | 上传文件（覆盖） |
| PATCH | `/api/v1/vms/<name>/inventory/<file>` | 更新文件（JSON 深度合并） |

### 上传文件

```json
POST /api/v1/vms/<name>/inventory
{
  "name": "my_file",
  "content_field_1": "...",
  "content_field_2": "..."
}
```

- 带 `name` 字段 → 保存为 `{name}.json`（覆盖同名文件）
- 不带 `name` 字段 → 保存为 `{timestamp}.json`

### 更新文件（深度合并）

只更新指定字段，保留未提及的字段不变。适合 Orchestrator 只更新 `data.network` 而不碰 `data.public_keys`：

```json
PATCH /api/v1/vms/<name>/inventory/wireguard_network.json
{
  "data": {
    "network": {
      "assigned_ip": "10.200.0.1/32",
      "listen_port": 51820,
      "dns_servers": ["8.8.8.8"],
      "peers": [
        {
          "publicKey": "aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890abcd=",
          "ip": "10.200.0.2/32",
          "endpoint": "192.168.1.105:51820"
        }
      ]
    }
  }
}
```

Peer 字段说明：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `publicKey` | string | 是 | WireGuard 公钥，44 字符 base64 |
| `endpoint` | string | 是 | 可达地址 `host:port` |
| `ip` | string | 否 | 单个 VPN IP（CIDR），等价于 `allowed_ips: [ip]` |
| `allowed_ips` | list[string] | 否 | 可达 IP 范围列表（CIDR），默认使用 `ip` |

### 获取文件

```
GET /api/v1/vms/<name>/inventory/<file>
```

响应：

```json
{
  "success": true,
  "data": {
    "content": { ... },
    "file": "my_file.json",
    "name": "my-vm",
    "timestamp": "2026-06-28T00:32:21.205814+00:00"
  }
}
```

`timestamp` 为文件最后修改时间，可用于变更检测。

---

## 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/utils/health` | 健康检查 |
| GET | `/api/v1/utils/mac` | 生成随机 MAC 地址 |
| GET | `/api/v1/bridges` | 列出网桥 |
| POST | `/api/v1/bridges/<name>` | 创建网桥 |
