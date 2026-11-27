# UniTao KVM Host REST Agent

## 安装依赖

部署脚本（见下文）会按需自动安装，一般不用手动运行。手动安装：

```bash
sudo ./src/kvm_install.sh   # 系统软件包（KVM、OVS、genisoimage、novnc、websockify）
sudo ./src/req_install.sh   # Python 依赖 → src/extlib/
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

全新部署和更新部署都只需运行这一个脚本。它会先按需安装依赖：

- 缺少任一系统依赖（`virsh`、`virt-install`、`qemu-img`、`genisoimage`、`brctl`、`ovs-vsctl`、`websockify`、noVNC）时运行 `src/kvm_install.sh`。
- `src/requirements.txt` 与上次安装时不同（按 sha256 比较，记录在 `src/extlib/.requirements.sha256`）或从未安装时运行 `src/req_install.sh`。

然后用 `src/rest/config.json` 作为模板调和运行时的 `config.json`（由 `service/merge_config.py` 完成）：

- 配置文件不存在时，直接复制模板。
- 已有配置缺少模板中的配置项（例如升级后新增的 `isoDataDir`）时，会在终端逐项询问，直接回车采用模板默认值；非交互运行时自动采用默认值并打印警告。
- 模板中没有的配置项会被删除。
- `hostKeyDir` 和 `hostApiUrl` 总是写为部署脚本检测到的值。

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

**基于已有镜像（`"local"`）：**

```json
POST /api/v1/images/<name>
{
  "imageFormat": "qcow2",
  "imageSource": "local",
  "baseImagePath": "../ubuntu-26.04.qcow2",
  "baseImageFormat": "qcow2"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| imageFormat | string | 是 | 镜像格式，通常 `qcow2` |
| imageSource | string | 是 | `"local"` |
| baseImagePath | string | 是 | 基镜像路径（相对于 `/opt/kvm/images/`） |
| baseImageFormat | string | 是 | 基镜像格式 |

**从远程下载（`"remote"`）：**

```json
POST /api/v1/images/<name>
{
  "imageFormat": "qcow2",
  "imageSource": "remote",
  "downloadLink": "https://cloud-images.ubuntu.com/releases/26.04/release/ubuntu-26.04-server-cloudimg-amd64.img"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| imageFormat | string | 是 | 镜像格式（`qcow2` 或 `raw`） |
| imageSource | string | 是 | `"remote"` |
| downloadLink | string | 是 | 远程镜像下载 URL |

等待 `downloadState` 变为 `"ready"` 后即可在 VM 创建时引用。

Image 创建完成后，其虚拟磁盘大小（`qemu-img info` 的 `virtual-size`，向上取整到 GB）会记录在 image JSON 的 `sizeInGB` 中；远程下载的 image 也会补上。之前创建、没有 `sizeInGB` 的 image，会在 `GET /api/v1/images/<name>` 或被 VM 当作 `osImage` 引用时探测并写回（下载中或失败的 image 不探测）。

---

## ISO 管理

ISO 是只读的安装介质，用于以 cdrom 方式挂载到 VM 上安装系统；它与 image 分开管理，不会被当作磁盘或 backing file 使用。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/kvm_iso` | 列出所有 ISO（含 `isoFileDir` 下未纳管的 `.iso` 文件） |
| GET | `/api/v1/kvm_iso/<name>` | 查看 ISO 详情（`downloadState`、`sizeInBytes`、`sha256`） |
| POST | `/api/v1/kvm_iso/<name>` | 从远程 URL 下载（异步） |
| PUT | `/api/v1/kvm_iso/<name>` | 上传 ISO 文件（流式写盘） |
| DELETE | `/api/v1/kvm_iso/<name>` | 删除 ISO 定义和文件 |

存储位置由 `config.json` 的 `isoDataDir`（定义 JSON）和 `isoFileDir`（`.iso` 文件）决定。`<name>` 只能包含字母、数字、`.`、`_`、`-`，不能以 `.`/`-` 开头，也不要带 `.iso` 后缀。

### 远程下载

```json
POST /api/v1/kvm_iso/<name>
{
  "downloadLink": "https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso",
  "sha256": "<可选，64 位十六进制>"
}
```

返回 202，后台下载；轮询 `GET /api/v1/kvm_iso/<name>` 直到 `downloadState` 为 `"ready"` 或 `"failed"`。提供 `sha256` 时下载完成后会校验，不一致则判为 `failed`。

### 上传

```bash
curl -T ubuntu.iso -H 'Content-Type: application/octet-stream' \
     'http://<host>:5000/api/v1/kvm_iso/<name>?sha256=<可选>'
```

请求体即 ISO 原始内容，需要 `Content-Length`。上传同步完成并返回 201，响应和定义 JSON 中都会记录实际文件的 `sha256`。

### 覆盖与删除

- 已存在且 `downloadState` 为 `"ready"` 的 ISO 不能直接覆盖（409 `CONFLICT`），需先 DELETE。
- `"failed"` 的 ISO 可直接重新 POST/PUT，残留文件会被清理。
- 传输中（`"in-progress"` 或下载进度对象）的 ISO 不能覆盖或删除（409）。
- 仍被 VM 引用（`installIso`）的 ISO 不能删除（409），需先对相关 VM 调用 `eject-iso`。
- 服务重启时，未完成的传输会被标记为 `"failed"` 并清理临时文件。

---

## VM 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/vms` | 列出所有 VM |
| POST | `/api/v1/vms` | 创建/更新 VM |
| GET | `/api/v1/vms/<name>` | 查看 VM 详情（virsh state、disk/net JSON、inventory） |
| PATCH | `/api/v1/vms/<name>` | 修改 VM（`cpu`、`ramInGB`、`bridge`） |
| DELETE | `/api/v1/vms/<name>` | 删除 VM |
| POST | `/api/v1/vms/<name>/start` | 启动 VM |
| POST | `/api/v1/vms/<name>/stop` | 停止 VM |
| POST | `/api/v1/vms/<name>/eject-iso` | 卸载安装 ISO（VM 需已停止） |
| GET | `/api/v1/vms/<name>/usb` | 列出 VM 配置的 USB 直通设备 |
| POST | `/api/v1/vms/<name>/usb` | 把主机 USB 设备挂到 VM（VM 需已停止），见 [USB 直通](#usb-直通) |
| DELETE | `/api/v1/vms/<name>/usb/<vendorId>:<productId>` | 卸下 USB 设备（VM 需已停止） |
| GET | `/api/v1/vms/<name>/block` | 列出 VM 配置的直通磁盘 |
| POST | `/api/v1/vms/<name>/block` | 把主机整块磁盘直通给 VM（VM 需已停止），见 [块设备直通](#块设备直通) |
| DELETE | `/api/v1/vms/<name>/block/<by-id 名>` | 卸下直通磁盘（VM 需已停止） |
| POST | `/api/v1/vms/<name>/commit` | 将 qcow2 disk 变更提交到 backing image |
| GET | `/api/v1/vms/<name>/console` | 网页 VNC 控制台（浏览器打开） |
| POST | `/api/v1/vms/<name>/console` | 为控制台页面启动一次性 websockify（页面自动调用） |

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
  "shareInventoryData": true,
  "prepareDomainImage": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | VM 唯一标识 |
| cpu | int | 是 | vCPU 数量 |
| ramInGB | int | 是 | 内存（GB） |
| vmHostName | string | 是* | VM hostname（`useCloudInit=false` 时不可提供） |
| osImage | string | 是* | Image 名称（使用 `installIso` 时可选，见 [从 ISO 安装](#从-iso-安装)） |
| osVariant | string | 是 | OS 类型（如 `ubuntu24.04`） |
| bridge | string | 是 | 网桥名称 |
| useCloudInit | bool | 否 | 是否使用 cloud-init 启动（默认 true；提供 `installIso` 时默认 false），见下方 [不使用 cloud-init](#不使用-cloud-init) |
| installIso | string | 否 | 用 `kvm_iso` 中的 ISO 安装系统，见下方 [从 ISO 安装](#从-iso-安装) |
| ipv4 | string | 否 | 静态 IP（CIDR 格式，如 `192.168.1.100/24`）；不提供则网卡使用 DHCP |
| gateway4 | string | 否 | 网关地址（与 `ipv4` 一起使用） |
| shareInventoryData | bool | 否 | 注入 inventory_tool.py 和 report_network.py |
| prepareDomainImage | bool | 否 | 注入 prep_image_for_commit.py |
| diskSizeGB | int | 否 | 磁盘大小（GB）。使用 `osImage` 时默认等于该 image 的 `sizeInGB`，且不能小于它（否则 400） |
| authType | string | 否 | 认证方式，见下方 [VM 认证方式](#vm-认证方式) |
| customerPWD | string | 否 | authType=CustomerPWD 时必填，明文密码 |
| customerKeys | list[string] | 否 | authType=CustomerKey 时必填，SSH 公钥列表 |

### 不使用 cloud-init

设置 `"useCloudInit": false` 时，VM 直接从磁盘启动，不生成、不挂载 cloud-init ISO：

```json
POST /api/v1/vms
{
  "id": "my-vm",
  "cpu": 2,
  "ramInGB": 2,
  "osImage": "my-prebuilt-image",
  "osVariant": "ubuntu24.04",
  "bridge": "ovs-br0",
  "useCloudInit": false
}
```

- `vmHostName` 不再必填。
- 以下字段依赖 cloud-init 生效，提供时返回 400 `VALIDATION_ERROR`：`vmHostName`、`ipv4`、`gateway4`、`authType`、`customerPWD`、`customerKeys`、`shareInventoryData=true`、`prepareDomainImage=true`。
- 网卡 JSON 只记录网桥和 MAC 地址；guest 的 IP 配置、登录凭据、hostname 都需要镜像自身预先配置好（官方 cloud image 默认没有密码/密钥，无法直接登录）。

### 从 ISO 安装

用已就绪的 `kvm_iso` 安装系统。系统盘有两种方式：

**方式一：安装后做成基础镜像（可 commit）**

先建一个空白基础镜像，VM 的系统盘是它的 overlay；安装完成后 `commit`，装好的系统就写入该镜像，之后其他 VM 可用 `osImage` 引用它。

```json
POST /api/v1/images/fnos-base
{"imageFormat": "qcow2", "imageSource": "local", "sizeInGB": 64}

POST /api/v1/vms
{
  "id": "fnos-prep",
  "cpu": 2,
  "ramInGB": 4,
  "osVariant": "debian12",
  "bridge": "ovs-br0",
  "osImage": "fnos-base",
  "installIso": "fnos-1.2"
}
```

安装完成后：`stop` → `eject-iso` → `commit`。

**方式二：独立空白盘（不可 commit）**

```json
POST /api/v1/vms
{
  "id": "my-vm",
  "cpu": 2,
  "ramInGB": 4,
  "osVariant": "ubuntu24.04",
  "bridge": "ovs-br0",
  "installIso": "ubuntu-24.04",
  "diskSizeGB": 40
}
```

系统盘是 `diskSizeGB` 大小、没有 backing file 的空白 qcow2，`diskSizeGB` 必填；`commit` 会返回 400（没有可写回的 backing image）。

通用说明：

- `installIso` 引用的 ISO 必须存在且 `downloadState` 为 `"ready"`。
- `useCloudInit` 默认为 false；显式设为 true 返回 400。其余 cloud-init 专用字段的限制同 [不使用 cloud-init](#不使用-cloud-init)。
- VM 创建后为 `stopped`，`POST /api/v1/vms/<name>/start` 启动后，在浏览器打开 [网页控制台](#网页控制台) `http://<host>:5000/api/v1/vms/<name>/console` 完成安装。
- 启动顺序为 `hd,cdrom`：空白盘无法启动，会进入 ISO 安装程序；安装完成重启后直接从系统盘启动，不会再次进入安装程序。
- 安装完成后，停止 VM 并调用 `POST /api/v1/vms/<name>/eject-iso` 卸载 ISO（从 VM JSON 中移除 `installIso`），之后启动不再挂载光驱。
- VM 仍引用某个 ISO 时，`DELETE /api/v1/kvm_iso/<name>` 返回 409。

### 网页控制台

在浏览器打开：

```
http://<host>:5000/api/v1/vms/<name>/console
```

页面由 REST 提供（noVNC），打开后自动调用 `POST /api/v1/vms/<name>/console`，REST 在 `consolePortMin`–`consolePortMax`（默认 6080–6099）中挑一个空闲端口启动：

```
websockify --run-once --timeout 30 <port> 127.0.0.1:<vncPort>
```

页面再通过 WebSocket 连到该端口。

- **一个页面一个进程**：`--run-once` 只代理一个 WebSocket 连接，连接断开（关闭页面、刷新、浏览器崩溃、断网）进程即退出；30 秒内没人连上也会退出。VM `stop` / `DELETE` 时会杀掉该 VM 仍在运行的控制台进程。
- 刷新页面或点 **Reconnect** 会开一个新会话；网络闪断后不会自动重连。
- 工具栏的 **Ctrl+Alt+Del** 按钮把组合键发给 VM。
- VM 必须在运行（否则 409 `VM_NOT_RUNNING`）。
- VM 的 VNC 只监听 `127.0.0.1`（`--graphics vnc,listen=127.0.0.1`），局域网无法直接连 5900，只能经控制台进入；也可以用 SSH 隧道：`ssh -L 5900:localhost:5900 <user>@<host>`。该设置在 VM 下次启动时生效，已在运行的 VM 仍保持原监听地址。
- 防火墙需放行 REST 端口（5000）和控制台端口段（默认 6080–6099/tcp）。

依赖（`src/kvm_install.sh` 已包含，部署时自动安装；noVNC 固定从 `/usr/share/novnc` 加载）：

```bash
sudo apt-get install -y novnc websockify
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `consolePortMin` | `6080` | websockify 端口段起点 |
| `consolePortMax` | `6099` | websockify 端口段终点（同时打开的控制台数上限） |

未安装 websockify / noVNC 或端口用尽时返回 503 `CONSOLE_UNAVAILABLE`。

### USB 直通

把主机上的 USB 设备（如移动硬盘）挂给 VM。**不支持热插拔**：挂载和卸下都要求 VM 已停止（`vmState` 为 `stopped` 且 virsh 中未运行，否则 409 `VM_RUNNING`），下次 `start` 时生效。

先用 [`GET /api/v1/devices?type=usb`](#主机设备) 找到设备，再按 `id` 或 `vendorId`/`productId` 挂载：

```json
POST /api/v1/vms/<name>/usb
{"device": "2-1"}
```

```json
POST /api/v1/vms/<name>/usb
{"vendorId": "0bc2", "productId": "3343"}
```

- 写入 VM JSON 的 `usbDevices`（`[{"vendorId", "productId", "description"}]`），每次启动 VM 时据此生成 `<hostdev type='usb'>`。返回 201。
- 按 `vendorId:productId` 匹配，所以换个 USB 口、重新插拔都不影响。主机上有两个 `vendorId:productId` 相同的设备时返回 409 `AMBIGUOUS_DEVICE`（无法区分）。
- 设备 `attachable` 为 `false` 时返回 409 `DEVICE_UNAVAILABLE`，原因同 `/api/v1/devices`：已挂在其他 VM 上、已配置在其他 VM 的 `usbDevices` 中（即使那个 VM 已停止）、USB 硬盘在主机上有挂载点等。挂载前先在主机上 `umount`。
- 设备不在主机上时返回 404 `DEVICE_NOT_FOUND`；已配置在本 VM 上时返回 200，`attached: false`（幂等）。
- 启动时设备不在（已拔掉）不影响 VM 启动（`startupPolicy='optional'`），插回后需要重启 VM 才会挂上。
- 每次启动时还会重新检查（见 [启动时检查](#启动时检查)）：设备已被其他 VM 占用、USB 硬盘在主机上有挂载点、或主机上出现了两个 `vendorId:productId` 相同的设备时，跳过该设备并记 WARNING，VM 照常启动。

卸下：

```
DELETE /api/v1/vms/<name>/usb/0bc2:3343
```

从 `usbDevices` 中移除，下次启动不再挂载。

**安全拔插流程**：在来宾里卸载文件系统 → `POST /api/v1/vms/<name>/stop` → `DELETE` 卸下（或保留配置）→ 物理拔出。插回：插上 → 确认 `attachable` 为 `true` → VM 已停止时 `POST` 挂载 → `start`。VM 运行中物理拔线等同于直接拔掉物理机上的硬盘，来宾若仍挂载着会丢数据。

`GET /api/v1/vms/<name>/usb` 返回每个已配置设备的 `present`（主机上是否插着）、`hostDevices`（对应的主机 USB `id`）、`liveAttached`（当前是否挂在运行中的 VM 上）。

### 块设备直通

把主机上的整块磁盘（SATA/NVMe/USB 硬盘）作为 virtio 磁盘交给 VM，性能比 USB 直通好，来宾里是 `/dev/vdX`。与 USB 直通一样**不支持热插拔**：挂载和卸下都要求 VM 已停止，下次 `start` 时生效。

先用 [`GET /api/v1/devices?type=block`](#主机设备) 找到磁盘，再按 `id` 或 by-id 路径挂载：

```json
POST /api/v1/vms/<name>/block
{"device": "sdb"}
```

```json
POST /api/v1/vms/<name>/block
{"path": "/dev/disk/by-id/usb-Seagate_Expansion_HDD_NAAB1-0:0"}
```

- 写入 VM JSON 的 `blockDevices`（`[{"path", "description"}]`）。`path` 一律记录 `/dev/disk/by-id/...`：按 `id` 挂载时取该磁盘的 `byIdPath`，`/dev/sdX` 会随插拔、重启变化，不能用。磁盘没有 by-id 链接时返回 409 `NO_STABLE_PATH`。
- 启动时生成 `--disk path=<by-id>,format=raw,bus=virtio,cache=none,io=native`。有直通盘时，系统盘和安装 ISO 使用显式的逐设备启动顺序（`boot_order`），直通盘不参与启动，避免固件从直通盘上的系统启动而跳过安装程序。
- 只能直通整块磁盘；by-id 路径指向分区（`-partN`）或不在列表中的设备时返回 400。
- 磁盘 `attachable` 为 `false` 时返回 409 `DEVICE_UNAVAILABLE`：主机上有挂载点（含 swap）、主机正在使用（已激活的 LVM/RAID/加密、holder、已导入的 ZFS 池等）、已挂在其他 VM 上（含其分区），或已配置在其他 VM 中。
- 同一块 USB 硬盘只能用一种方式挂到一个 VM：以块设备配置后，它的 USB 条目也不可挂，反之亦然。
- 不在主机上时返回 404 `DEVICE_NOT_FOUND`；已配置在本 VM 上（任一 by-id 链接指向同一块盘）时返回 200，`attached: false`。
- 启动时磁盘不在（如 USB 硬盘已拔掉）、已不是整块磁盘、在主机上有挂载点或正在被主机使用、或已被其他 VM 占用时，跳过它并记 WARNING，VM 照常启动（见 [启动时检查](#启动时检查)）。

卸下（`<by-id 名>` 是 `path` 的最后一段，也是 `GET` 返回的 `key`）：

```
DELETE /api/v1/vms/<name>/block/usb-Seagate_Expansion_HDD_NAAB1-0:0
```

`GET /api/v1/vms/<name>/block` 返回每个已配置磁盘的 `key`、`present`（by-id 路径是否存在）、`hostDevice`（当前对应的内核设备名）、`liveAttached`。

安全拔插流程同 [USB 直通](#usb-直通)：来宾里卸载 → `stop` → `DELETE`（或保留配置）→ 物理拔出。

### 启动时检查

挂载接口的检查只在挂载那一刻有效：之后主机可能挂载了这块盘、另一个 VM 可能先启动占用了它、或插上了相同型号的第二个 USB 设备。所以每次 `start` 生成 domain 时，会对 `usbDevices` / `blockDevices` 再做一次同样的检查（`KvmVm.resolve_passthrough()`），不安全的设备跳过并记 WARNING（`VM [<vm>] starts without ...`），VM 照常启动。跳过的原因（以及被忽略的格式错误条目）同时出现在 `POST /api/v1/vms/<name>/start` 返回的 `data.warnings` 中。另一个 domain 直通了某块盘的分区时，整块盘也视为被占用。多个 VM 同时启动时，从检查到 `virsh create` 是串行的，不会两个 VM 都拿到同一个设备。`lsblk` 不可用时无法检查主机占用，所有直通磁盘和带磁盘的 USB 设备都会被跳过，日志原因为 `lsblk failed`。

挂载/卸下时如果 VM 还有遗留的已停止 persistent domain，会先 `virsh undefine` 再改 VM JSON；undefine 失败（如带 UEFI nvram）时返回 500 并且不改 JSON，需先手工 `virsh undefine --nvram <vm>`。否则下次 `start` 会直接启动旧 domain，改动不生效。手工配置的 USB hub 不会被直通（启动时跳过并给出警告），因为那会把 hub 后面的所有设备都从主机拿走。VM JSON 由原子写入替换，需要目录的写权限；以 root 运行时保留原文件的所有者。

只有 `start` 才会生成 domain：期望状态为 `stopped` 时（`POST /vms` 新建、对已停止的 VM 调 `stop`、PATCH 调和等），准备 cloud-init ISO 和磁盘镜像，并运行 `virt-install --print-xml` 校验定义、写出 `vm_def_<vm>.xml`（不含直通设备），但不执行 `virsh create`。以前这些操作会让 VM 开机一瞬间再断电，直通设备会被来宾短暂占用，可能损坏数据。定义错误仍在 `POST /vms` 时暴露。

另外：

- 挂载接口在检查占用和写 VM JSON 期间持有一把所有 VM 共用的锁，两个请求同时把同一设备挂给不同 VM 时只有一个成功。
- virsh 状态查询失败时，挂载/卸下返回 500 `SYSTEM_COMMAND_FAILED`，不在不确定 VM 是否运行时修改配置。
- 同一 VM 已以另一种方式配置了这块 USB 硬盘时返回 409，提示先从 `/usb` 或 `/block` 中 DELETE。
- VM JSON 中格式不对的条目（手工编辑造成：大写十六进制、`/dev/sdX` 路径、重复等）不会阻止 `start`/`stop`/`delete`：启动时跳过并记 WARNING（`Ignore [usbDevices] entry ...`），JSON 原样保留。`GET /vms/<name>/usb|block` 中以 `"key": null, "error": ...` 列出，需手工修正 JSON。`usbDevices`/`blockDevices` 整个不是列表时，该 VM 的挂载/卸下返回 409 `INVALID_VM_DATA`，其他 VM 和 `GET /api/v1/devices` 不受影响。
- 某个 VM 的 JSON 读不出来时，它占用了哪些设备无从得知：`GET /api/v1/devices` 多出 `errors.claims`，所有设备都标为不可挂，挂载接口一律拒绝。VM JSON 的写入（REST 和 `KvmVm` 写回 cloud-init 登录信息）都是原子的（临时文件 + rename，保留原文件权限），正常读写不会读到写了一半的文件；扫描期间被删除的 VM 视为没有占用。
- `lsblk` 不可用时，`POST /vms/<name>/block` 返回 500 `SYSTEM_COMMAND_FAILED`。

### VM 认证方式

通过 `authType` 字段控制 cloud-init 注入的认证策略：

| authType | 密码 | SSH 密钥 | ssh_pwauth | 说明 |
|----------|:---:|:---:|:---:|------|
| `CustomerPWD` | 客户指定 | — | true | 明文密码登录 |
| `RandomPWD` | 随机生成 | — | true | 自动生成安全随机密码 |
| `HostKey` | 无 | Host 公钥 | false | Host RSA 密钥对免密登录 |
| `CustomerKey` | 无 | 客户公钥 | false | 客户提供 SSH 公钥列表 |
| `NoAuth` | 无 | 无 | false | 无认证，全自动 VM |
| 未声明 | 无 | 无 | 不设置 | 不做任何密码/密钥设置 |

### Commit Image

```json
POST /api/v1/vms/<name>/commit
{}
```

要求 VM 已停止且 virsh state 为 `notExists` 或 `shutOff`。

### 修改 VM（PATCH）

只支持修改一级标量字段，且 VM 必须处于停止状态：

```json
PATCH /api/v1/vms/my-vm
{
  "cpu": 4,
  "ramInGB": 8,
  "bridge": "ovs-br0"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `cpu` | int | 正整数，写入 vm JSON 的 `smp` |
| `ramInGB` | int | 正整数，写入 vm JSON 的 `ramInGB` |
| `bridge` | string | 必须是已管理的网桥；`bridgeType` 自动同步到 net JSON |

约束：

- 以数据文件中的期望状态 `vmState` 为准：期望 `running` 时返回 `409 VM_RUNNING`。
- 期望状态与 live virsh 状态不一致时，先用 `KvmVm.Process()` 调和到一致；调和失败返回 `500 SYSTEM_COMMAND_FAILED`。
- 非法字段返回 `400 INVALID_PATCH_FIELD`，并在 `error.details` 中返回 PATCH schema 白名单。
- 当前只支持单网卡；VM 的 `networks` 数组长度不为 1 时返回 `501 NOT_IMPLEMENTED`。
- 修改后会删除 `vm_def_*.xml`、`cloud_init.iso`、`ci_data/` 等生成文件，下次 `POST /start` 时按新数据重建。磁盘镜像和 `data/**` 不会被删除。
- 完整的 PATCH schema 可通过 `GET /api/v1/schema` 获取。

响应示例：

```json
{
  "success": true,
  "data": {
    "name": "my-vm",
    "updated": ["cpu", "bridge"],
    "networkFile": "net-br0.json",
    "removedGeneratedFiles": ["/opt/kvm/vms/my-vm/vm_def_my-vm.xml"],
    "vmState": "stopped",
    "warnings": []
  }
}
```

### Schema

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/schema` | 聚合返回 `src/data/schema/*/rest.json` 的内容 |

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
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `publicKey` | string | 是 | WireGuard 公钥，44 字符 base64 |
| `endpoint` | string | 是 | 可达地址 `host:port` |
| `ip` | string | 否 | 单个 VPN IP（CIDR），等价于 `allowed_ips: [ip]` |
| `allowed_ips` | list[string] | 否 | 可达 IP 范围列表（CIDR），默认使用 `ip` |
| `persistentKeepalive` | int | 否 | 保活包间隔（秒），默认 25 |
| `presharedKey` | string | 否 | 预共享密钥（PSK） |
| `description` | string | 否 | 备注，写入 wg.conf 注释 |
| `disabled` | bool | 否 | `true` 跳过该 peer |
| `id` | string/int | 否 | 内部标识符 |

Agent 每轮 poll 会上报 status 到独立文件 `wireguard_network_status.json`：

```json
{"status": [{"key": "state", "value": "up"}, {"key": "recv_bytes", "value": "1234"}, {"key": "sent_bytes", "value": "5678"}]}
```

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

## 主机设备

列出主机上可以挂给 VM 的设备（只读，不做挂载）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/devices` | 列出 USB 设备和整块磁盘 |
| GET | `/api/v1/devices?type=usb` | 只列 USB 设备（`type=block` 只列磁盘） |
| GET | `/api/v1/devices?attachable=true` | 只列当前可挂载的设备 |

两类设备对应两种挂法：

- **`usb`**：USB 直通（`<hostdev type='usb'>`，按 `vendorId:productId` 匹配），来宾看到的是真实 USB 设备。来自 `/sys/bus/usb/devices`，hub（含 root hub）不列出。
- **`block`**：块设备直通（`<disk type='block'>`），来宾看到的是一块磁盘。来自 `lsblk`，只列整块磁盘，`loop`/`zram`/`nbd`/`sr`/`dm-`/`md` 不列出。挂载时用 `byIdPath`（`/dev/disk/by-id/...`），设备重新插拔后 `/dev/sdX` 可能变化，by-id 路径不会。

USB 硬盘在两边都会出现，并互相关联（USB 的 `blockDevices` ↔ 磁盘的 `usbDevice`）。

每个设备都带 `attachable`；为 `false` 时 `reason` 说明原因：

| 原因 | 说明 |
|------|------|
| `attached to VM [<vm>]` | 已挂在某个 libvirt domain 上（`virsh dumpxml` 中有对应的 `hostdev`/`disk`）。USB 硬盘无论以哪种方式挂出，两边都会标记 |
| `configured on VM [<vm>]` | 已写入某个受管 VM JSON 的 `usbDevices` 或 `blockDevices`（见 [USB 直通](#usb-直通)、[块设备直通](#块设备直通)）。该 VM 停止时没有 domain，但下次启动会再次占用。`configuredOn` 列出这些 VM；USB 硬盘无论以哪种方式配置，两边都会标记 |
| `mounted on host: ...` | 磁盘或其分区/LV 在主机上有挂载点（含 `[SWAP]`） |
| `in use by host: ...` | 主机正在使用：已激活的 LVM/加密/RAID 等子设备；磁盘或分区上叠有内核 holder（`/sys/block/<dev>/holders` 非空，如 dm/md/bcache）；所属 ZFS 池已在主机上导入（`zpool list`；`zpool` 命令失败时保守地视为占用）；或是多盘 btrfs 中未显示挂载点的成员（与已挂载的 btrfs 同 UUID）。只有签名（如 `LVM2_member`、`crypto_LUKS`）而未激活不算：来宾可能在自己的直通盘上建了这些结构 |
| `cannot check VM attachments: ...` | `virsh list` 或某个 domain 的 `virsh dumpxml` 失败，无法确认设备是否已被 VM 占用（同时出现在 `errors.attached`）。主机上没有 virsh 时视为没有 domain |
| `cannot check host use of ...: lsblk failed` | USB 设备带有磁盘，但 `lsblk` 不可用，无法确认主机是否在用 |
| `cannot read VM JSON of [<vm>] ...` | 某个受管 VM 的 JSON 读不出来，设备占用情况未知（同时出现在 `errors.claims`） |

USB 设备下的光驱等不在 `block` 列表中的设备（如 `sr0`）也会检查：光盘在主机上挂载着时，该 USB 设备不可挂。

主机和 VM 同时写同一块盘会损坏数据，所以挂载前先在主机上 `umount`。

```json
GET /api/v1/devices
{
  "success": true,
  "data": {
    "usb": [
      {
        "id": "2-1",
        "vendorId": "0bc2", "productId": "3343",
        "bus": 2, "device": 3,
        "manufacturer": "Seagate", "product": "Expansion HDD", "serial": "NAAB1",
        "speedMbps": 5000,
        "classes": ["massStorage"],
        "blockDevices": ["sdb"],
        "configuredOn": [],
        "attachedTo": null, "attachable": true, "reason": null
      }
    ],
    "block": [
      {
        "id": "sdb",
        "path": "/dev/sdb",
        "byIdPath": "/dev/disk/by-id/usb-Seagate_Expansion_HDD_NAAB1-0:0",
        "sizeBytes": 4000787030016,
        "model": "Expansion HDD", "serial": "NAAB1",
        "transport": "usb", "removable": false,
        "usbDevice": "2-1",
        "partitions": [{"name": "sdb1", "type": "part", "fsType": "ntfs", "sizeBytes": 4000785104896, "mountpoints": []}],
        "mountpoints": [], "hostUse": [],
        "configuredOn": [],
        "attachedTo": null, "attachable": true, "reason": null
      }
    ]
  }
}
```

USB 的 `id` 是 sysfs 端口路径（`<bus>-<port>[.<port>]`），换个 USB 口就会变；磁盘的 `id` 是内核设备名。`virsh` 不可用时 `attachedTo` 一律为 `null`（日志中有 WARNING）。`lsblk` 不可用时 `block` 为空列表，并多出 `"errors": {"block": "lsblk failed: ..."}`。`speedMbps` 为数字（如 `480`、`1.5`），读不到时为 `null`。

命令行同样可用：`./src/runpy.sh src/kvm/device/kvm_device.py [--type usb|block]`。

---

## 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/utils/health` | 健康检查 |
| GET | `/api/v1/utils/mac` | 生成随机 MAC 地址 |
| GET | `/api/v1/bridges` | 列出网桥 |
| POST | `/api/v1/bridges/<name>` | 创建网桥 |
