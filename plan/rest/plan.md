# REST 模块规划

## 目标

在每台 KVM 宿主机上部署 Flask Agent，通过 REST API 替代 CLI，使中心控制器可以远程管理虚拟机。

## 技术选型

- **框架**: Flask（轻量，够用）
- **数据存储**: 保持 JSON 文件模式
- **认证**: 暂不添加（内网使用）

## API 端点

```
GET    /api/v1/vms              — 列出所有 VM + virsh 状态
POST   /api/v1/vms              — 创建/更新 VM
GET    /api/v1/vms/<name>       — 获取 VM 详情 + 运行状态
DELETE /api/v1/vms/<name>       — 删除 VM
POST   /api/v1/vms/<name>/start — 启动 VM
POST   /api/v1/vms/<name>/stop  — 停止 VM

GET    /api/v1/images           — 列出所有镜像
POST   /api/v1/images           — 创建镜像
GET    /api/v1/images/<name>    — 获取镜像详情

GET    /api/v1/bridges          — 列出所有桥接
POST   /api/v1/bridges          — 创建/更新桥接
GET    /api/v1/bridges/<name>   — 获取桥接详情
DELETE /api/v1/bridges/<name>   — 删除桥接

GET    /api/v1/utils/health     — 健康检查
GET    /api/v1/utils/mac        — 生成随机 MAC 地址
```

## 文件结构

```
src/rest/
    __init__.py
    app.py           # Flask 应用工厂 + 错误处理
    service.py       # JSON 文件持久化层
    api_vm.py        # VM Blueprint
    api_image.py     # Image Blueprint
    api_bridge.py    # Bridge Blueprint
    api_utils.py     # 工具 Blueprint
    requirements.txt # Flask==3.1
    restapp.sh       # 启动脚本
    data/            # 默认数据目录
        vm/
        image/
        bridge/
```

## 对现有代码的重构

- `KvmVm.__init__` 接受可选 `data_path` 参数（向后兼容 CLI）
- `NetBridge` 新增 `Create()`、`Delete()`、`Process()` 方法

## 启动方式

```bash
# 安装 Flask
pip install --target src/extlib flask

# 启动 REST Agent
./src/rest/restapp.sh
# 服务监听 0.0.0.0:5000

# 自定义数据目录
UNITAO_DATA_DIR=/var/lib/unitiao/data ./src/rest/restapp.sh
```
