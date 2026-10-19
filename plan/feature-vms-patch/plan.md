# Feature: VMS的PATCH方法

## 背景

endpoint vms是否支持patch?我想修改vm的bridge属性怎么做?

结论：**当前不支持**。

- `/api/v1/vms` → 只支持 `GET`、`POST`（`src/rest/api_vm.py:156,180`）
- `/api/v1/vms/<name>` → 只支持 `GET`、`DELETE`（`src/rest/api_vm.py:372,586`）
- 唯一支持 `PATCH` 的是 inventory 文件：`PATCH /api/v1/vms/<name>/inventory/<file>`（`src/rest/api_vm.py:531`，schema 里也只列了这一条 `src/rest/api_schema.py:25`）

另外，`POST /api/v1/vms` 虽然文档写着“创建/更新”，但也改不了已存在 VM 的 bridge：

1. `create_vm()` 只要发现同名 VM 仍存在于 `virsh list --name --all` 中，就会直接返回，不会重建域定义（`src/kvm/vm/kvm_vm.py:491-494`）。
2. `sync_vm_state()` 只负责启动/停止，不负责重新生成 XML（`src/kvm/vm/kvm_vm.py:542-551`）。bridge 是在创建时由 `virt-install --print-xml` 写进域 XML 的（`src/kvm/vm/kvm_vm.py:502-515`），之后不会根据数据文件变化重建。

### bridge 属性在哪里

VM 的 `networks` 指向 `{vmDataDir}/<vm>/data/net-<bridge>.json`：

```json
{
  "ifaceType": "bridge",
  "bridgeName": "br0",
  "bridgeType": "linuxBridge",
  "macAddress": "0e:..."
}
```

`bridgeName`、`bridgeType` 定义在 `KvmNetwork.Keyword`（`src/kvm/vm/kvm_vm.py:556-557`）。如果同时修改 `bridgeName` 和 `bridgeType`，还要保证 `bridgeType` 与目标网桥一致：OVS 网桥会额外生成 `virtualport_type=openvswitch`（`src/kvm/vm/kvm_vm.py:670-675`）。

### 暂时的解决办法

这些 VM 是用 `virsh create` 从生成的 XML 创建的瞬态域，`destroy` 后域就会消失，所以可以“停 → 改数据文件 → 再起”：

```bash
# 1. 停（stop 端点会 virsh destroy，瞬态域随之消失）
curl -X POST http://<host>:<port>/api/v1/vms/<name>/stop

# 2. 在宿主机直接编辑（REST 没有写这个文件的接口）
vi {vmDataDir}/<name>/data/net-<old-bridge>.json
#    bridgeName 改成目标网桥
#    桥类型变了就同时改 bridgeType（linuxBridge / ovsBridge）
#    不要改 macAddress —— Cloud-Init network-config 靠它绑定 IP

# 3. 重新起：POST start 会把 vmState 置为 running，Process 发现域已不存在，
#    于是用改过的 net JSON 重新 virt-install --print-xml + virsh create
curl -X POST http://<host>:<port>/api/v1/vms/<name>/start
```

第 3 步也可以用 CLI 完成：

```bash
./src/runpy.sh src/kvm/vm/kvm_vm.py --path {vmDataDir}/<name>/data/vm-<name>.json
```

**不建议**用 `POST /api/v1/vms` 来改：它会重新生成 `net-<新桥>.json`（随机 MAC、新文件名）并重写 disk JSON（`src/rest/api_vm.py:326-347`），会丢失原有 MAC 和部分手工字段；而且仍要求目标 VM 的域已不在 virsh 中。

---

## 目标

给 endpoint 增加 PATCH 方法：

1. 创建 `src/data/schema` 目录。
2. 创建 `src/data/schema/vms/rest.json`，描述 vms 的 REST schema。
3. 创建 `api/v1/schema` endpoint，用来显示所有 `rest.json` 内容。
4. 在 vms 的 PATCH schema 中定义允许更改的字段。
5. 创建 `src/data/schema/vms/rest_map.json`，记录 REST API 字段到 data/ 数据文件字段的映射关系。
6. 创建 `src/data/schema/vms/data_map.json`，记录 data/ 数据文件字段到 `{vm}/` 生成文件的映射关系。
7. PATCH 逻辑：
   - 7.1 VM 状态为 start/active（running）时不允许 PATCH。
   - 7.2 PATCH 包含非法字段时报错，并返回 VM 的 PATCH schema。
   - 7.3 PATCH 修改完 data/ 数据文件后，按照 `data_map.json` 的映射删除生成文件，这样 VM 再次启动时新数据可以及时生效。

### 本次范围（已确认）

- **只开放 3 个字段**：`cpu`、`ramInGB`、`bridge`。
- **只修改一级（直接）属性**：不允许数组、字典、子对象等二级数据的修改逻辑。
- PATCH 允许字段以 **POST `/api/v1/vms` 的 request schema** 为准；POST 中的 `bridge` 是标量，PATCH 也保持标量，不引入 `network` selector 等新概念。
- 状态判断**复用现有代码**，不新增 virsh 封装。

---

## 设计

### 1. 目录与文件

```text
src/data/
└── schema/
    └── vms/
        ├── rest.json      # vms 资源端点 + PATCH 字段白名单和校验规则
        ├── rest_map.json  # REST 字段 → data/*.json 一级字段
        └── data_map.json  # data/*.json 一级字段 → {vm}/ 下的生成文件
```

不新增 Python 包（不需要 `__init__.py`）。schema 由 REST 进程在运行时按文件路径读取，目录随仓库一起部署；`service/deploy-service.sh` 使用 `{{REPO_ROOT}}` 直接运行仓库代码，无需额外拷贝步骤。

加载根目录统一由 `src/rest/schema_loader.py` 计算：

```python
SCHEMA_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "schema")
)
```

### 2. `src/data/schema/vms/rest.json`

描述 vms 的所有端点和 PATCH 契约。**只定义“允许改什么、如何校验”**，字段落盘位置由 `rest_map.json` 负责，避免两处维护。

```json
{
  "resource": "vms",
  "basePath": "/api/v1/vms",
  "version": 1,
  "endpoints": {
    "GET    /api/v1/vms": "List all VMs (managed + virsh-only)",
    "POST   /api/v1/vms": "Create a VM",
    "GET    /api/v1/vms/<name>": "Get VM details",
    "PATCH  /api/v1/vms/<name>": "Partially update a stopped VM (fields: cpu, ramInGB, bridge)",
    "DELETE /api/v1/vms/<name>": "Delete VM",
    "POST   /api/v1/vms/<name>/start": "Start VM",
    "POST   /api/v1/vms/<name>/stop": "Stop VM",
    "POST   /api/v1/vms/<name>/commit": "Commit disk changes to backing image",
    "GET    /api/v1/vms/<name>/inventory": "List inventory files",
    "GET    /api/v1/vms/<name>/inventory/<file>": "Get inventory file",
    "POST   /api/v1/vms/<name>/inventory": "Upload inventory file",
    "PATCH  /api/v1/vms/<name>/inventory/<file>": "Deep-merge inventory file"
  },
  "patch": {
    "description": "Partial update of a managed, non-running VM. Only top-level scalar fields that also appear in the POST /api/v1/vms request schema are accepted.",
    "source": "POST /api/v1/vms request schema",
    "allowedFields": {
      "cpu":    {"type": "integer", "minimum": 1},
      "ramInGB": {"type": "integer", "minimum": 1},
      "bridge": {"type": "string", "minLength": 1, "format": "managedBridge"}
    },
    "immutableFields": {
      "id":        "VM identity is the JSON filename",
      "osImage":   "Changing the base image is a rebuild, not a patch",
      "osVariant": "Not in scope for this iteration",
      "ipv4":      "Not in scope for this iteration",
      "gateway4":  "Not in scope for this iteration",
      "disks":     "Disk topology/top-level array is not patchable",
      "networks":  "Network topology/top-level array is not patchable",
      "vmPath":    "Path is derived from vmDataDir/<name>",
      "vmState":   "Use POST /start and /stop",
      "useCloudInit": "Cloud-init mode is fixed at creation",
      "ciIsoPath": "Generated artifact path"
    }
  }
}
```

> `allowedFields` 是唯一白名单。它与 POST request schema 的交集只有 `cpu`、`ramInGB`、`bridge` 三个一级标量字段；后续要开放更多字段时，只需按同一模式在 `rest.json` / `rest_map.json` / `data_map.json` 三处补充。

### 3. `src/data/schema/vms/rest_map.json`

记录 REST 字段 → data 文件 + **一级字段路径**。

```json
{
  "resource": "vms",
  "version": 1,
  "files": {
    "vm": {
      "path": "{vmDataDir}/{vmName}/data/vm-{vmName}.json",
      "description": "Main VM definition (vm JSON)"
    },
    "network": {
      "path": "resolve(vm JSON networks[0])",
      "description": "The single network interface JSON; current REST API supports exactly one bridge"
    }
  },
  "fields": {
    "cpu":     {"file": "vm", "path": "smp"},
    "ramInGB": {"file": "vm", "path": "ramInGB"},
    "bridge": {
      "file": "network",
      "path": "bridgeName",
      "derive": {
        "path": "bridgeType",
        "from": "bridgeEntity",
        "field": "bridgeType"
      }
    }
  }
}
```

约定：

- `path` 必须是单层字段名，不涉及数组、字典、子对象路径。
- `bridge` 是唯一的联动字段：写入 `bridgeName` 时，从目标 bridge 实体的数据中读取 `bridgeType` 并同步写入。这样做是数据一致性要求，**不是**开放给调用方的二级数据修改能力；调用方仍然只传 `{"bridge": "br1"}`。
- 网络文件通过 `vm JSON.networks[0]` 解析得到（支持 `{vmPath}` 与相对路径），**PATCH 不改写 `networks` 数组，也不重命名 `net-<bridge>.json` 文件**。文件名沿用创建时的名字，文件内容才是事实来源。

### 4. `src/data/schema/vms/data_map.json`

记录 data 一级字段 → `{vmPath}/` 下生成文件的映射，供 PATCH 后失效旧产物。

```json
{
  "resource": "vms",
  "version": 1,
  "artifacts": {
    "vmDefScript": "{vmPath}/vm_def_create_{vmName}.sh",
    "vmDefXml":    "{vmPath}/vm_def_{vmName}.xml",
    "ciIso":       "{ciIsoPath}",
    "ciDataDir":   "{vmPath}/ci_data"
  },
  "dependencies": {
    "vm": {
      "smp":     ["vmDefScript", "vmDefXml", "ciIso", "ciDataDir"],
      "ramInGB": ["vmDefScript", "vmDefXml", "ciIso", "ciDataDir"]
    },
    "network": {
      "bridgeName": ["vmDefScript", "vmDefXml", "ciIso", "ciDataDir"],
      "bridgeType": ["vmDefScript", "vmDefXml", "ciIso", "ciDataDir"]
    }
  },
  "neverDelete": {
    "dataDir":      "data/** — source JSON definitions",
    "inventoryDir": "data/inventory/** — VM-reported data",
    "requestJson":  "data/request.json — original create request (traceability)",
    "diskImages":   "imagePath resolved from disk JSON — deleting is data loss",
    "ciYamlFiles":  "user-data.yaml / meta-data.yaml / network-config.yaml are overwritten by create_ci_iso on next start"
  },
  "notPatchable": {
    "disk": ["imagePath", "imageSource", "imageFormat", "sizeInGB", "baseImagePath", "baseImageFormat"]
  }
}
```

约定：

- 模板变量：`{vmPath}`（VM 根目录，即 `vm-<name>.json` 中的 `vmPath`）、`{vmName}`、`{ciIsoPath}`（vm JSON 中的实际值）。
- `ciIso` / `ciDataDir` 出现在依赖里，是因为 `create_ci_iso()` 会把 vm JSON 和 net JSON 复制到 ISO 内做自文档化（`src/kvm/vm/kvm_vm.py:235-242`）。
- **删除仅限解析后位于 `{vmPath}` 内的路径**；`ciIsoPath` 若被配置到 VM 目录外则跳过并记录 warning。
- 磁盘镜像文件（disk JSON 的 `imagePath`）**永不删除**。
- 删除是 best-effort：文件不存在视为成功；单个删除失败记录 warning 并继续，最终在响应中返回 `warnings`。

### 5. `GET /api/v1/schema`

在 `src/rest/api_schema.py` 中新增路由（该 Blueprint 已注册在 `/api/v1`，见 `src/rest/app.py:110`）：

```text
GET /api/v1/schema
```

行为：

- 遍历 `src/data/schema/*/rest.json`，按目录名作为 key 聚合返回。
- 保持现有响应格式：`{"success": true, "data": {...}}`。
- 单个文件缺失或 JSON 解析失败时记录 warning 并跳过，不影响其他资源。
- 现有 `GET /api/v1`（`_API_SCHEMA`）保持不变，以兼容旧调用方；可在其 `data` 中附加 `"schemaUrl": "/api/v1/schema"`。

响应示例：

```json
{
  "success": true,
  "data": {
    "vms": { "...": "src/data/schema/vms/rest.json 的完整内容" }
  }
}
```

### 6. `PATCH /api/v1/vms/<name>`

#### 6.1 请求

```http
PATCH /api/v1/vms/my-vm
Content-Type: application/json

{
  "bridge": "br1"
}
```

也可以一次修改多个允许字段：

```json
{
  "cpu": 4,
  "ramInGB": 8,
  "bridge": "ovs-br0"
}
```

- 请求体只接受一级标量字段；嵌套对象 / 数组一律按非法字段或校验失败处理。
- 不引入 `network` selector。当前 REST API 创建的 VM 只有一个网卡，PATCH 始终作用于 `networks[0]`。
- 不提供 `null` 删除语义：`cpu`、`ramInGB`、`bridge` 都是必填标量；传 `null` → `400 VALIDATION_ERROR`。

#### 6.2 允许修改的字段

| REST 字段 | 写入位置 | 校验 |
|---|---|---|
| `cpu` | vm JSON `smp` | 正整数（≥1） |
| `ramInGB` | vm JSON `ramInGB` | 正整数（≥1） |
| `bridge` | net JSON `bridgeName` + `bridgeType` | 非空字符串；必须存在于 managed bridges；`bridgeType` 自动同步 |

不可 PATCH：`id`、`osImage`、`osVariant`、`ipv4`、`gateway4`、`useDHCP4`、`authType`、`customerPWD`、`customerKeys`、`shareInventoryData`、`prepareDomainImage`、`disks`、`networks`、`vmPath`、`vmState`、`useCloudInit`、`ciIsoPath`、`macAddress`。

#### 6.3 处理流程

1. 校验 `Content-Type: application/json`，body 必须是 JSON object。
2. 读取 `rest.json` / `rest_map.json` / `data_map.json`（在 `schema_loader.py` 中按路径加载，可加进程内缓存）。
3. 读取 VM 定义；不存在 → `404 NOT_FOUND`。
4. 状态检查（对应目标 7.1）——**复用 `src/rest/api_vm.py:36` 的 `_get_virsh_state(name)`**，不新增 virsh 封装：
   - 返回 `Running` → `409 VM_RUNNING`。
   - 返回 `"unknown"`（virsh 调用失败）→ `500 SYSTEM_COMMAND_FAILED`。
   - VM 数据 `vmState == notExists` → `409 VM_NOT_EXISTS`（数据处于待删除状态，不接收修改）。
5. 剩余字段为空 → `400 EMPTY_PATCH`。
6. 字段白名单校验（对应目标 7.2）：
   - 未在 `allowedFields` 中 → `400 INVALID_PATCH_FIELD`，错误中附带 `patch` schema（见 6.5）。
   - 类型 / 长度 / 最小值不符合 → `400 VALIDATION_ERROR`。
   - `bridge` 通过 `read_entity_data(current_app, "bridge", name)` 确认存在；不存在 → `400 VALIDATION_ERROR`。
7. 解析目标文件（只处理一级字段）：
   - `vm` → `vm-<name>.json`。
   - `network` → 读取 vm JSON 的 `networks`；要求长度恰好为 1。若长度不为 1，返回 `501 NOT_IMPLEMENTED`（当前 REST API / PATCH 只实现单网桥，无法判断应修改哪一块网卡），并在 message 中写明实际长度和原因。按 `{vmPath}` / 相对路径解析得到 net JSON。
8. 在内存中计算目标文件的最终内容：
   - `cpu` → `smp`，`ramInGB` → `ramInGB`。
   - `bridge` → `bridgeName`，并从 managed bridge 数据中取 `bridgeType` 同步写入。
   - 与现值相同则视为 no-op。
9. 原子写入（临时文件 + `os.replace`），写入前备份原内容。
10. 写完后用 `KvmVm(logger, vm_json_path)` 构造一次（只做 `Validate`，不执行命令，见 `src/kvm/vm/kvm_vm.py:78-102`）；失败则用备份恢复所有已写文件并返回 `400 VALIDATION_ERROR`。
11. 失效生成文件（对应目标 7.3）：
    - 根据实际发生变化的字段，取 `data_map.dependencies` 中 artifacts 的并集。
    - 展开模板后删除文件 / 目录（`ciDataDir` 用 `shutil.rmtree`）。
    - 具体删除项通过 `data_map.json` 驱动，不在代码里硬编码。
12. 一致性兜底：
    - managed VM 使用 `virsh create` 创建瞬态域，`stop` 后域已不存在，无需额外操作。
    - 若 `_get_virsh_state(name)` 返回 `Stopped`，说明域是持久化的且仍留在 `virsh list --name --all` 中；此时执行一次 `virsh undefine <name>`（通过现有 `Util.run_command`），确保下一次 `POST /start` 走 `create_vm()` 重建 XML 的路径。
13. 返回结果。

#### 6.4 成功响应

```json
{
  "success": true,
  "data": {
    "name": "my-vm",
    "updated": ["cpu", "bridge"],
    "networkFile": "net-br0.json",
    "removedGeneratedFiles": [
      "/opt/kvm/vms/my-vm/vm_def_create_my-vm.sh",
      "/opt/kvm/vms/my-vm/vm_def_my-vm.xml",
      "/opt/kvm/vms/my-vm/cloud_init.iso",
      "/opt/kvm/vms/my-vm/ci_data"
    ],
    "vmState": "stopped",
    "warnings": []
  }
}
```

- `updated`：REST 字段列表。
- `networkFile`：实际被修改的 net JSON 文件名（文件名保持不变）。
- no-op（值未变化）：`updated: []`，不删除任何生成文件。
- 数据文件已更新但部分生成文件删除失败：仍返回 `200`，失败项放入 `warnings`；数据文件本身是唯一事实来源。

#### 6.5 错误响应

非法字段（对应目标 7.2）：

```json
{
  "success": false,
  "error": {
    "code": "INVALID_PATCH_FIELD",
    "message": "Unsupported PATCH field(s): ['foo', 'bar']",
    "details": {
      "allowedFields": ["cpu", "ramInGB", "bridge"],
      "immutableFields": ["id", "osImage", "osVariant", "ipv4", "gateway4",
                          "useDHCP4", "authType", "customerPWD", "customerKeys",
                          "shareInventoryData", "prepareDomainImage", "disks",
                          "networks", "vmPath", "vmState", "useCloudInit",
                          "ciIsoPath", "macAddress"],
      "schemaUrl": "/api/v1/schema"
    }
  }
}
```

`details` 直接来自 `rest.json` 的 `patch` 节点，保证返回内容与白名单永不漂移。

多网卡（内部实现限制）：

```json
{
  "success": false,
  "error": {
    "code": "NOT_IMPLEMENTED",
    "message": "PATCH on multiple networks is not implemented: VM 'my-vm' has 2 entries in networks[], but only a single bridge is supported"
  }
}
```

其他错误码：

| HTTP | code | 场景 |
|---|---|---|
| 400 | `BAD_REQUEST` | Content-Type / JSON 不合法 |
| 400 | `EMPTY_PATCH` | body 中没有任何可修改字段 |
| 400 | `INVALID_PATCH_FIELD` | 出现白名单外字段 |
| 400 | `VALIDATION_ERROR` | 字段值 / bridge 存在性校验失败 |
| 404 | `NOT_FOUND` | VM 不存在 |
| 409 | `VM_RUNNING` | VM 正在运行 |
| 409 | `VM_NOT_EXISTS` | VM 数据处于 `notExists` |
| 500 | `SYSTEM_COMMAND_FAILED` | virsh 查询失败 |
| 501 | `NOT_IMPLEMENTED` | VM 数据的 `networks` 长度不为 1（当前只实现单网桥 PATCH） |

> `409` 为新增状态码；错误响应结构沿用现有 `{"success": false, "error": {...}}`，`details` 为可选扩展，不破坏旧调用方。

### 7. 代码改动清单

| 文件 | 改动 |
|---|---|
| `src/data/schema/vms/rest.json` | 新增：端点描述 + PATCH 字段白名单/校验规则（cpu、ramInGB、bridge） |
| `src/data/schema/vms/rest_map.json` | 新增：REST 字段 → data 文件一级字段映射 |
| `src/data/schema/vms/data_map.json` | 新增：data 一级字段 → VM 生成文件映射 |
| `src/rest/schema_loader.py` | 新增：加载/缓存三份 schema、模板展开、文件删除、映射一致性检查 |
| `src/rest/api_schema.py` | 新增 `GET /schema`，聚合所有 `rest.json` |
| `src/rest/api_vm.py` | 新增 `PATCH /<name>` 及字段校验、原子写入、生成文件失效；状态判断复用现有 `_get_virsh_state()` |
| `src/rest/app.py` | 新增 `501` JSON error handler，保证 501 响应结构与现有错误格式一致 |
| `src/rest/README.md` | 补充 PATCH 和 `/api/v1/schema` 文档 |
| `README.md` | API 表格补充 PATCH 行 |
| `src/rest/api_schema.py` 的 `_API_SCHEMA` | 补充 `PATCH /vms/<name>` 条目（保留旧结构） |

实现原则：

- PATCH 处理逻辑完全由 schema 文件驱动；新增/删除可 PATCH 字段只需改 JSON。
- 仅支持一级标量字段；不实现通用 JSON Pointer / 嵌套合并逻辑。
- `schema_loader.py` 在加载时做一致性检查：`rest_map.fields` 的 key 必须 ⊆ `rest.json.patch.allowedFields`；`data_map.dependencies` 必须覆盖 `rest_map` 的所有目标字段。不一致时记录启动日志 warning（不阻塞服务），便于发现配置漂移。
- 不修改现有的 `POST /vms`、`start`、`stop`、`commit` 行为。

### 8. 边界情况

- **VM 正在运行**：直接拒绝，不做 CPU/RAM 热插拔，也不做网桥热迁移；调用方必须先 `POST /stop`。
- **VM 未托管**（virsh 中存在但没有 `data/vm-<name>.json`）：PATCH 返回 `404`，与 GET/DELETE 的 managed 语义一致。
- **单网卡假设**：`networks` 数组长度必须为 1；多网卡数据返回 `501 NOT_IMPLEMENTED`，message 中写明“当前只实现单网桥 PATCH，VM 实际有 N 个 networks”，不猜测要改哪一块。
- **`bridge` 与 `bridgeType`**：只接受 managed bridge；`bridgeType` 从 bridge 实体读取，不允许调用方指定。
- **文件名保持不变**：把 `bridgeName` 从 `br0` 改成 `br1` 后，net JSON 仍叫 `net-br0.json`；VM JSON 的 `networks` 引用不变。这是为了遵守“只改一级字段、不碰数组”的约束。
- **生成文件删除范围**：严格限制在 `{vmPath}` 内；磁盘镜像永不删除；`data/**` 与 `inventory/**` 永不删除。
- **生效时机**：`cpu` / `ramInGB` / `bridge` 都通过重建域 XML 生效，因此必须在下一次 `POST /start` 时生效；PATCH 本身不启动 VM。
- **并发**：`api_vm.py` 内按 VM 名加 `threading.Lock`，避免 PATCH 与 start/stop 并发写同一份 JSON。

### 9. 手工验证

仓库没有测试框架，使用 curl + 宿主机检查：

1. `GET /api/v1/schema` 返回 `vms` 节点，内容与 `src/data/schema/vms/rest.json` 一致。
2. 创建并停止 VM 后：
   ```bash
   curl -X PATCH http://127.0.0.1:5000/api/v1/vms/my-vm \
        -H 'Content-Type: application/json' \
        -d '{"bridge":"br1"}'
   ```
   - 检查 `data/net-*.json` 的 `bridgeName` / `bridgeType` 已更新。
   - 检查 `networks[0]` 引用的文件名没有变化。
   - 检查 `vm_def_*.xml`、`cloud_init.iso`、`ci_data/` 已删除。
   - `POST /start` 后 `virsh dumpxml my-vm | grep -A3 interface` 显示新网桥。
3. `PATCH {"cpu": 4, "ramInGB": 8}` 后重启，`virsh dumpxml` 中 vCPU / 内存已更新。
4. VM 运行时 PATCH → `409 VM_RUNNING`。
5. PATCH `{"foo": 1}` 或 `{"ipv4": "..."}` → `400 INVALID_PATCH_FIELD`，`error.details` 包含白名单。
6. PATCH 值非法（`{"cpu": 0}`、`{"bridge": "not-exist"}`、`{"ramInGB": null}`）→ `400`，且 data 文件保持原样。
7. 对 `networks` 长度不为 1 的 VM 执行 PATCH → `501 NOT_IMPLEMENTED`，错误 message 中包含实际 `networks` 数量和原因。

### 10. 后续可扩展（非本次目标）

- 开放 `osVariant`、`ipv4`、`gateway4`、`useDHCP4`、`authType` 等一级字段。
- 多网卡场景的 `network` selector。
- PATCH 返回完整的 VM 详情（当前只返回变更摘要）。
- 基于 schema 自动生成 `POST /vms` 的字段校验，消除创建/更新两套规则。
- 审计日志：在 `data/patch_history.jsonl` 记录每次 PATCH 的字段差异。
