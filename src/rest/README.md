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

## API

### 健康检查

```
GET /api/v1/utils/health
```

### VM 管理

```
GET    /api/v1/vms              # 列出所有 VM
POST   /api/v1/vms              # 创建 VM
GET    /api/v1/vms/<name>       # 查看 VM 详情
DELETE /api/v1/vms/<name>       # 删除 VM
POST   /api/v1/vms/<name>/start # 启动 VM
POST   /api/v1/vms/<name>/stop  # 停止 VM
```

创建 VM 请求示例：

```json
{
    "vm_id": "my-vm",
    "cpu_number": 2,
    "os_name": "ubuntu24.04",
    "os_size": 30
}
```
