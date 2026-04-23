# OpenViking 加密套件总结

## 概览

OpenViking 采用**信封加密（Envelope Encryption）**架构，结合 HKDF 密钥派生和 AES-256-GCM 对称加密，实现多租户环境下的透明静态数据加密。本文档对项目中涉及的所有加密算法、库依赖和密钥管理方案做统一梳理。

---

## 1. 加密算法清单

| 用途 | 算法 / 构造 | 参数 | 所在模块 |
|------|------------|------|---------|
| 文件内容加密 / File Key 加密 | **AES-256-GCM** | 密钥 32 字节，IV 12 字节，无 AAD | `openviking/crypto/encryptor.py`、`openviking/crypto/providers.py` |
| 账户密钥派生（Root Key → Account Key） | **HKDF-SHA-256** | salt=`openviking-kek-salt-v1`，info=`openviking:kek:v1:{account_id}`，输出 32 字节 | `openviking/crypto/providers.py` |
| API Key 哈希存储 | **Argon2id** | time_cost=3, memory_cost=65536 (64 MiB), parallelism=2, hash_len=32 | `openviking/server/api_keys.py` |
| API Key 明文比较 | **HMAC 常数时间比较** | `hmac.compare_digest` | `openviking/server/api_keys.py` |
| Root Key / File Key / API Key 生成 | **CSPRNG** | `secrets.token_bytes(32)` / `secrets.token_hex(32)` | 多处 |
| Vault Transit 密钥类型 | **aes256-gcm96** | 由 Vault 服务端管理 | `openviking/crypto/providers.py` |
| 内容指纹 / 去重 ID | SHA-256 / MD5 | `hashlib` 标准库 | 存储层各处 |
| 快速非加密哈希 | **xxHash** | 字符串→uint64 映射 | `openviking/storage/vectordb/utils/str_to_uint64.py` |
| CLI HTTPS 通信 | **Rustls TLS 栈** | `reqwest` + `rustls-tls` feature | `crates/ov_cli/Cargo.toml` |

---

## 2. 三层密钥体系

```
┌──────────────────────────────────────────────────────────┐
│  Layer 1: Root Key（根密钥）                              │
│  • 全局唯一，256-bit                                     │
│  • 存储方式取决于 Provider（本地文件 / Vault / 火山KMS）    │
│  • 用途：派生所有 Account Key                             │
└────────────────────┬─────────────────────────────────────┘
                     │ HKDF-SHA-256 派生
                     ▼
┌──────────────────────────────────────────────────────────┐
│  Layer 2: Account Key（账户密钥 / KEK）                   │
│  • 每个账户独立，256-bit                                  │
│  • 不持久化，运行时按需派生                                │
│  • 用途：加密/解密 File Key                               │
└────────────────────┬─────────────────────────────────────┘
                     │ AES-256-GCM 封装
                     ▼
┌──────────────────────────────────────────────────────────┐
│  Layer 3: File Key（文件密钥 / DEK）                      │
│  • 每次写入随机生成，256-bit                               │
│  • 加密后存储在文件头（信封）中                             │
│  • 用途：加密实际文件内容                                  │
└──────────────────────────────────────────────────────────┘
```

---

## 3. 依赖库

### Python 端

| 库 | 版本要求 | 用途 |
|----|---------|------|
| `cryptography` | >=42.0.0 | HKDF-SHA-256 密钥派生、AES-256-GCM 加解密（`AESGCM`） |
| `argon2-cffi` | >=23.0.0 | API Key 的 Argon2id 哈希与验证 |
| `hvac` | 可选 (test) | HashiCorp Vault 客户端（`VaultProvider`） |
| `volcengine` | 核心依赖 | 火山引擎 KMS HTTP API 客户端（`VolcengineKMSProvider`） |
| `xxhash` | 核心依赖 | 快速非加密哈希 |
| 标准库 `hashlib`, `hmac`, `secrets` | — | SHA-256/MD5 指纹、常数时间比较、CSPRNG |

### Rust 端（`ov` CLI）

| Crate | 用途 |
|-------|------|
| `reqwest` + `rustls-tls` | HTTPS 客户端，使用 Rustls TLS 栈（非 OpenSSL） |
| `getrandom` | 操作系统级随机数 |

---

## 4. 密钥提供程序（Root Key Provider）

### 4.1 Local（本地文件）

- **存储**：`~/.openviking/master.key`（hex 编码的 32 字节密钥）
- **文件权限**：`0600`（仅所有者可读写）
- **初始化**：`ov system crypto init-key -o ~/.openviking/master.key`
- **适用场景**：开发环境、单节点部署

### 4.2 Vault（HashiCorp Vault）

- **Transit Engine**：密钥类型 `aes256-gcm96`，名称默认 `openviking-root-key`
- **KV Engine**：加密后的 Root Key 存储于 KV 路径 `openviking-encrypted-root-key`
- **支持 KV v1 和 v2**
- **适用场景**：生产环境、多云部署

### 4.3 Volcengine KMS（火山引擎）

- **Root Key**：由 KMS 服务端加密，本地缓存于 `~/.openviking/openviking-volcengine-root-key.enc`
- **API**：`Encrypt` / `Decrypt`（Version 2021-02-18）
- **通信**：HTTPS 到 `kms.{region}.volcengineapi.com`
- **适用场景**：火山引擎云部署

---

## 5. 信封文件格式（OVE1）

加密文件使用统一的二进制信封格式：

```
偏移    大小     字段
0       4B      Magic: "OVE1" (0x4F564531)
4       1B      Version: 0x01
5       1B      Provider Type: 0x01=Local, 0x02=Vault, 0x03=Volcengine
6       2B      Encrypted File Key Length (big-endian)
8       2B      Key IV Length (big-endian)
10      2B      Data IV Length (big-endian)
12      变长    Encrypted File Key
—       变长    Key IV
—       变长    Data IV (12B)
—       变长    Encrypted Content (AES-256-GCM ciphertext + 16B auth tag)
```

未加密文件不以 `OVE1` 开头，解密时直接返回明文（向后兼容）。

---

## 6. API Key 安全

| 机制 | 细节 |
|------|------|
| 生成 | `secrets.token_hex(32)`，256-bit 熵 |
| 存储（加密启用时） | Argon2id 哈希 + VikingFS 加密持久化 |
| 存储（加密未启用时） | 明文 JSON |
| 验证 | `argon2.PasswordHasher.verify()` 或 `hmac.compare_digest` 常数时间比较 |
| Argon2id 参数 | time_cost=3, memory_cost=64 MiB, parallelism=2, hash_len=32 |

---

## 7. TLS / 传输层安全

| 场景 | TLS 方案 |
|------|---------|
| `ov` CLI → 服务端 | Rustls（reqwest + rustls-tls），不链接 OpenSSL |
| Python HTTP 客户端 | httpx / requests，使用平台默认 TLS 配置 |
| FastAPI/Uvicorn 服务端 | 不配置自定义 cipher suite，TLS 通常由反向代理（Nginx/Ingress）终止 |
| Kubernetes 部署 | `ingress.tls` 配置 Kubernetes TLS Secrets |

---

## 8. 未使用的算法

以下算法在代码库中**未找到**使用：

- RSA / EC 非对称加密
- 数字签名（无 JWT/JWS）
- SM2 / SM3 / SM4（国密算法）
- 自定义 TLS cipher suite 列表

---

## 9. 关键代码路径

| 路径 | 职责 |
|------|------|
| `openviking/crypto/__init__.py` | 模块公共导出 |
| `openviking/crypto/encryptor.py` | `FileEncryptor`：信封构建/解析、AES-GCM 加解密 |
| `openviking/crypto/providers.py` | `LocalFileProvider` / `VaultProvider` / `VolcengineKMSProvider`：HKDF 派生 + Root Key 管理 |
| `openviking/crypto/config.py` | `validate_encryption_config` / `bootstrap_encryption` / `encryption_health_check` |
| `openviking/crypto/exceptions.py` | 加密异常类型定义 |
| `openviking/service/core.py` | 启动时初始化加密、注入 `FileEncryptor` |
| `openviking/storage/viking_fs.py` | VikingFS 读写路径接入 `encrypt_bytes` / `decrypt_bytes` |
| `openviking/server/api_keys.py` | `APIKeyManager`：Argon2id 哈希、加密持久化 |
| `openviking/metrics/datasources/encryption.py` | 加密操作可观测性指标 |
| `openviking_cli/utils/config/encryption_config.py` | Pydantic 配置模型 |
| `tests/unit/crypto/` | 加密模块单元测试 |
| `tests/integration/test_*encryption*` | 加密集成测试（Vault / Volcengine） |

---

## 10. 配置参考

在 `~/.openviking/ov.conf` 中的 `encryption` 字段：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local | vault | volcengine_kms",
    "local": { "key_file": "~/.openviking/master.key" },
    "vault": { "address": "...", "token": "...", "..." : "..." },
    "volcengine_kms": { "key_id": "...", "region": "...", "..." : "..." }
  }
}
```

详细配置说明见 [配置指南](../guides/01-configuration.md) 和 [加密指南](../guides/08-encryption.md)。
