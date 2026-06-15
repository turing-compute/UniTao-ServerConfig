# Feature: VM Secure Access 功能规划

## 背景

在当前设置下，通过ServerConfig创建的VM中的VM数据文件定义defaultPWD来设置VM的登录密码。
密码明码保存在json文件中有安全问题。

## 目标

解决创建的VM的安全问题：
1. VM 密码随机生成，不再硬编码
2. 部署时生成 Host Key Pair，保存在运行目录
3. VM 密码使用 Host 公钥加密后存储
4. 提供解密工具方便人工访问
5. Cloud-init 注入 Host SSH 公钥，支持私钥免密登录
6. VM 管理 API 需要提供加密的 Access Key

## 步骤进度

| 步骤 | 状态 | 内容 |
|------|------|------|
| 1 | `[ ]` | `src/requirements.txt` — 添加 cryptography 依赖 |
| 2 | `[ ]` | `src/security/__init__.py` — 创建 security 包 |
| 3 | `[ ]` | `src/security/key_manager.py` — RSA 密钥管理、加解密 |
| 4 | `[ ]` | `src/security/password_gen.py` — 随机密码生成 |
| 5 | `[ ]` | `src/security/generate_keys.py` — 部署用密钥生成脚本 |
| 6 | `[ ]` | `src/rest/config.json` — 添加 keyDir 配置项 |
| 7 | `[ ]` | `src/rest/app.py` — 初始化 KeyManager |
| 8 | `[ ]` | `src/kvm/vm/kvm_vm.py` — 密码解密 + SSH Key 注入 cloud-init |
| 9 | `[ ]` | `src/rest/api_vm.py` — 随机密码 + 加密存储 + Access Key 验证 |
| 10 | `[ ]` | `src/security/access_control.py` — API 访问控制 |
| 11 | `[ ]` | `src/security/decrypt_tool.py` — CLI 解密工具 |
| 12 | `[ ]` | `service/deploy-service.sh` — 部署时自动生成密钥 |

> **图例**: `[ ]` 待开始 `[~]` 进行中 `[x]` 已完成

## 下一步

**→ 步骤 1**: `src/requirements.txt` — 添加 `cryptography>=41.0.0`

---

## 技术选型

- **密钥类型**: RSA 4096（同时支持 SSH 认证 + 数据加密）
- **加密方式**: RSA-OAEP + SHA-256，通过 `cryptography` 库
- **密码前缀**: `ENC:` 标识加密密码，向后兼容明文密码
- **Access Key**: 客户端用 Host 公钥加密 VM 密码作为 `X-VM-Access-Key` header，服务端解密后比对

---

## 详细设计

### 步骤 1: `src/requirements.txt`

添加一行: `cryptography>=41.0.0`

### 步骤 2: `src/security/__init__.py`

空文件，Python 包标记。

### 步骤 3: `src/security/key_manager.py`

核心加密类 `KeyManager`:

- `__init__(key_dir)` — 指定密钥目录，自动创建
- `keys_exist() → bool` — 检查 PEM 文件是否存在
- `generate_keys()` — 生成 RSA 4096 密钥对，私钥权限 600
- `load_keys()` / `is_loaded()` — 加载密钥到内存
- `encrypt(plaintext: str) → str` — RSA-OAEP-SHA256 加密，返回 `ENC:` + base64
- `decrypt(ciphertext_b64: str) → str` — 解密 base64（不含前缀）
- `decrypt_if_encrypted(value: str) → str` — 有 `ENC:` 前缀则解密，否则原样返回
- `is_encrypted_value(value) → bool` — 判断是否 `ENC:` 前缀
- `get_public_key_openssh() → str` — 返回 OpenSSH 格式公钥，用于 cloud-init

常量: `ENCRYPTED_PREFIX = "ENC:"`

密钥文件命名: `host_private_key.pem` / `host_public_key.pem`

### 步骤 4: `src/security/password_gen.py`

- `generate_password(length=20) → str`
- 使用 `secrets.choice()`（密码学安全随机）
- 字符集: `ascii_letters + digits + "!@#$%^&*()-_=+"`

### 步骤 5: `src/security/generate_keys.py`

CLI 脚本，供 `deploy-service.sh` 调用:
- 接受一个参数: key_dir 路径
- 调用 `KeyManager(key_dir).generate_keys()`

### 步骤 6: `src/rest/config.json`

添加 `"keyDir": "/opt/unitiao/keys"`

### 步骤 7: `src/rest/app.py`

- `_DIR_KEYS` 列表添加 `"keyDir"`（自动解析相对路径 + 自动创建目录）
- `create_app()` 中初始化 `KeyManager`，存入 `app.config["KEY_MANAGER"]`
- Key pair 不存在时 warn 日志，不阻止启动

### 步骤 8: `src/kvm/vm/kvm_vm.py`

- `__init__` 增加 `key_dir` 参数，默认 `/opt/unitiao/keys`
- 新增 `_get_key_manager()` — 懒加载 KeyManager
- 新增 `_resolve_password(value)` — `ENC:` 前缀解密，否则原样返回 + warn 明文存储
- **`create_ci_user_data()` 变更**:
  - 密码部分：调用 `_resolve_password()` 获取明文写入 cloud-init
  - 新增：`ssh_authorized_keys` 注入 Host 公钥
  ```
  ssh_authorized_keys:
    - ssh-rsa AAAA... host@unitiao
  ```

### 步骤 9: `src/rest/api_vm.py`

新增 helper:
- `_get_key_manager()` — 从 app.config 获取 KeyManager
- `_create_vm_instance(logger, vm_path)` — 封装 key_dir 传递

**`_gen_vm_json()` 变更**:
- 调用 `generate_password()` 生成随机密码
- 调用 `km.encrypt()` 加密，`defaultPWD` 存储 `ENC:...`

**POST create 响应变更**:
- 返回 `accessKey` 字段（即加密后的密码），供客户端后续 API 调用

**Access Key 验证**（添加到 5 个端点）:
- `GET /<name>`
- `DELETE /<name>`
- `POST /<name>/start`
- `POST /<name>/stop`
- `PATCH ""`

不受保护的端点: `GET /api/v1/vms` (list) 和 `POST /api/v1/vms` (create)

### 步骤 10: `src/security/access_control.py`

```python
def verify_vm_access(vm_name: str) -> tuple[bool, str | None]:
    # 1. 获取 KeyManager，不存在则放行 (True, None)
    # 2. 读取 X-VM-Access-Key header
    # 3. 从 VM JSON 读取 defaultPWD
    # 4. 解密 access key → 解密 stored PWD → 比对
    # 5. 返回 (True, None) 或 (False, error_msg)
```

错误码: 403 ACCESS_DENIED

### 步骤 11: `src/security/decrypt_tool.py`

CLI 工具:
- `--data <base64>` — 直接解密
- `--file <vm.json>` — 提取 `defaultPWD` 并解密
- `--key-dir` — 指定密钥目录，默认 `/opt/unitiao/keys`

示例: `./src/runpy.sh src/security/decrypt_tool.py --file /opt/kvm/vms/mail01/data/vm-mail01.json`

### 步骤 12: `service/deploy-service.sh`

在创建 runtime dir（步骤1）和复制 config（步骤3）之间插入:

```bash
# 2.5 Generate host key pair
KEY_DIR="$RUNTIME_DIR/keys"
if [ ! -f "$KEY_DIR/host_private_key.pem" ]; then
    echo "Generating host key pair (RSA 4096)..."
    sudo "$REPO_ROOT/src/runpy.sh" "$REPO_ROOT/src/security/generate_keys.py" "$KEY_DIR"
    sudo chmod 600 "$KEY_DIR/host_private_key.pem"
    echo "Key pair generated in $KEY_DIR"
fi
```

---

## 数据流

### VM 创建
```
POST {id,cpu,ram,...}
  → generate_password() → "aB3$xY..."
  → encrypt("aB3$xY...") → "ENC:QmFzZTY..."
  → JSON: {"defaultPWD": "ENC:QmFzZTY..."}
  → KvmVm.Process()
    → _resolve_password("ENC:...") → "aB3$xY..."
    → user-data.yaml: password: aB3$xY... + ssh_authorized_keys: [host_pubkey]
    → genisoimage → cloud_init.iso
  → Response: { accessKey: "ENC:QmFzZTY..." }
```

### API 访问验证
```
GET /api/v1/vms/mail01 + X-VM-Access-Key: ENC:QmFz...
  → decrypt("ENC:QmFz...") → "aB3$xY..."
  → decrypt_if_encrypted(vm_json.defaultPWD) → "aB3$xY..."
  → 匹配 → 200 OK
```

---

## 向后兼容

| 场景 | 行为 |
|------|------|
| 新 VM + 有 Key | 完整加密 |
| 新 VM + 无 Key | `_gen_vm_json()` 抛 ValueError，提示运行 deploy-service.sh |
| 旧 VM（明文 defaultPWD） | `_resolve_password()` 原样返回 + warn，`verify_vm_access()` 明文比对 |
| Key 不存在 + 旧 VM | 所有 API 放行，无需 access key |
| 私钥丢失 | 新 VM 无法创建；旧 VM 仍可用（access key 本身即 `ENC:` 值）；解密工具失效 |

---

## 验证方式

1. **加解密验证**: `generate_password()` → `encrypt()` → `decrypt()` 一致
2. **Cloud-init 验证**: `user-data.yaml` 包含 `password:` 和 `ssh_authorized_keys:`
3. **API 验证**: 无 Key POST 报错 / 有 Key 返回 accessKey / 错误 key → 403
4. **解密工具**: `decrypt_tool.py --file vm-xxx.json` 输出明文密码
5. **部署验证**: `deploy-service.sh` 生成 `host_private_key.pem`(600) + `host_public_key.pem`
