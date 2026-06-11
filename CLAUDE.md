# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目概述

UniTao-ServerConfig 是一个数据驱动的服务器配置自动化工具。它将**配置数据**（定义期望状态的 JSON 文件）与**执行逻辑**（将数据转换为系统命令的 Python 脚本）分离。其理念更接近 Terraform（期望状态调和）而非 Ansible（过程式任务列表）。

目前主要领域是 **KVM 虚拟化**——在 Linux KVM 主机上创建虚拟机、磁盘镜像和网络桥接。

## 如何运行

```bash
# 安装 Python 依赖（wget）到 src/extlib/
./src/req_install.sh

# 安装 KVM 所需的系统软件包（qemu-kvm、libvirt、genisoimage、openvswitch 等）
./src/kvm_install.sh

# 运行任意组件 — runpy.sh 设置 PYTHONPATH=src/，然后用你的参数执行 python3
./src/runpy.sh src/kvm/vm/kvm_vm.py --path src/kvm/vm/example/vm.json
./src/runpy.sh src/kvm/image/kvm_image.py --path <磁盘数据文件路径.json>
./src/runpy.sh src/network/bridge/net_bridge.py --path <桥接数据文件路径.json>

# 生成随机 MAC 地址
./src/runpy.sh src/network/bridge/generate_mac.py
```

此仓库中**没有测试、没有 lint 配置、也没有构建步骤**。

## 架构

### 核心模式：JSON 数据 → Python → 系统命令

每个组件都遵循相同的模式：
1. 接受一个 `--path` 参数，指向描述期望状态的 **JSON 数据文件**。
2. JSON 文件名（不含 `.json`）通过 `Util.file_data_name()` 成为**实体名称**（虚拟机名、镜像名、桥接名）。
3. 验证 JSON 结构和值的合法性。
4. 将验证后的数据转换为 shell 命令（`virsh`、`virt-install`、`qemu-img`、`brctl`、`ip`、`genisoimage` 等），通过 `Util.run_command()` 执行。

### 共享库 (`src/shared/`)

- **`utilities.py`** — `Util` 类，包含静态方法：`read_json_file()`（读取 JSON 文件）、`run_command()`（subprocess 封装，非零退出时抛出 `SystemError`）、`abs_path()`、`file_data_name()`（从文件路径提取实体名称）、`write_file()`、`compare_dict()`、`is_int_str()`、`parse_mac_address()`。
- **`logger.py`** — `Log.get_logger(name, log_file, level)` 返回配置好的 `logging.Logger`，支持控制台输出和可选的日志文件输出。重新调用时会清除重复的 handler。

### KVM 虚拟机管理 (`src/kvm/vm/kvm_vm.py`)

最核心的模块。包含两个类：

- **`KvmVm`** — 虚拟机生命周期管理器。负责：
  1. 验证虚拟机 JSON 定义（vCPU、RAM、磁盘、网络、操作系统类型/变体、期望状态、Cloud-Init 设置）。
  2. 解析相对路径 — 支持 `{vmPath}` 占位符，在验证时替换为实际的虚拟机目录路径。
  3. `create_vm()`：生成 `virt-install --print-xml` 命令，写入 XML 定义文件，然后调用 `virsh create` 创建虚拟机。
  4. `sync_vm_state()`：通过 `virsh destroy` / `virsh start` 调和虚拟机的运行/停止状态。
  5. `delete_vm()`：如果虚拟机存在则调用 `virsh destroy` 销毁。
  6. Cloud-Init 支持：生成 `user-data`、`meta-data` 和 `network-config` YAML 文件，通过 `genisoimage` 打包成 `cidata` ISO 文件，并作为 cdrom 挂载。同时将虚拟机 JSON 和网络 JSON 包含在 ISO 中以便自文档化。
  7. `hostCPU` 关键字：当存在且为 `true` 时，向 virt-install 添加 `--cpu host` 以启用 CPU 直通模式。

- **`KvmNetwork`** — 虚拟机网络接口配置。支持两种接口类型：
  - `bridge`：连接到 Linux 网桥或 OVS 网桥。OVS 网桥会添加 `virtualport_type=openvswitch`。
  - `macvtap`：直连接口 tap，桥接模式。
  - 生成 Cloud-Init `network-config` v2 格式的 YAML 配置，包含静态 IP、网关、可选的路由度量值和 DNS 服务器。

### KVM 镜像管理 (`src/kvm/image/kvm_image.py`)

- **`KvmImage`** — 从两种来源创建磁盘镜像：
  - `remote`：通过 `wget` 从 `downloadLink` 下载。
  - `local`：使用 `qemu-img create` 创建（支持 `qcow2` 和 `raw`/`img` 格式），可选基于基础镜像（`-b`/`-F` 参数），可选指定大小。
- 镜像是幂等的 — 如果 `imagePath` 处文件已存在，则跳过创建。

### 网络桥接 (`src/network/bridge/`)

- **`net_bridge.py`** — 网络桥接的数据模型和验证。支持 `linuxBridge` 和 `ovsBridge` 类型及其关联的接口列表。**注意：** 此模块仅验证 JSON 数据，目前不执行桥接创建命令（与 `kvm_vm.py` 不同）。
- **`generate_mac.py`** — 打印一个随机本地管理 MAC 地址（OUI 前缀 `0E:`）。

### 归档代码 (`src/Archive/`)

早期迭代/遗留代码：
- **`shared/entity.py`** — 一个实体框架（`Entity`、`EntityOp`、`DataProvider`），用于当前状态与期望状态的调和循环。这是最初设计的架构，但当前活跃模块未使用。
- **`network/brctl/brctl.py`** — 使用实体框架的完整网桥生命周期操作（创建、删除、添加/移除接口、设置 MAC 地址）。
- **`network/veth/veth.py`** — 虚拟以太网对的创建/删除。

### 关键设计决策

- **无配置管理框架** — 原始 Python 脚本直接调用系统二进制文件。不使用 Ansible、Salt 或 Terraform provider。
- **基于文件的实体标识** — JSON 文件名即为实体标识。重命名文件会改变实体名称。
- **`{vmPath}` 占位符** — 虚拟机 JSON 可以使用 `{vmPath}` 语法引用相对于虚拟机目录的路径，在验证时解析。
- **无 Python 打包** — 脚本直接运行。`runpy.sh` 设置 `PYTHONPATH`，使 import 能从 `src/` 工作。外部库通过 `pip install --target` 安装到 `src/extlib/`。
- **`Util.run_command()` 在非零退出时始终抛出异常** — 没有错误恢复机制；失败会以 `SystemError` 向上传播。
