# 可插拔加密引擎设计文档

> **状态**：设计完成，待评审
> **作者**：AI Assistant
> **日期**：2026-04-23

---

## 1. 背景与动机

### 1.1 当前问题

OpenViking 的加密模块（`openviking/crypto/`）使用 Python `cryptography` 库进行 AES-256-GCM 加解密和 HKDF-SHA-256 密钥派生。该库**静态链接**了 OpenSSL，这意味着：

1. **无法使用 KAE 2.0 硬件加速**：鲲鹏服务器上的 KAE（Kunpeng Accelerator Engine）加速引擎需要通过**系统安装的 OpenSSL** 加载，静态链接的 OpenSSL 无法感知系统 ENGINE/PROVIDER。
2. **加密实现硬编码**：`providers.py` 和 `encryptor.py` 中直接 `from cryptography import ...` 并内联加解密逻辑，无法替换底层实现。
3. **缺乏扩展性**：如果未来需要支持国密算法（SM4）、其他硬件加速器或纯软件优化引擎，需要大量改动现有代码。

### 1.2 目标

1. 实现**可插拔的加密引擎**抽象，将对称加密和密钥派生操作与具体实现解耦
2. 提供 **KAE 引擎**，通过 ctypes 直接调用系统 `libcrypto.so`，加载 KAE 硬件加速
3. 同时支持 **OpenSSL 1.1.1**（ENGINE API）和 **OpenSSL 3.x**（OSSL_PROVIDER API）
4. **完全向后兼容**：不改变配置即使用默认引擎，行为与改造前一致

---

## 2. 设计总览

### 2.1 架构图

```
                    ┌──────────────────────┐
                    │     ov.conf          │
                    │  encryption.engine:  │
                    │    type: "kae"       │
                    │    engine_id: "kae"  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   bootstrap_encryption│  (config.py)
                    │   create_crypto_engine│
                    └──────────┬───────────┘
                               │
                ┌──────────────▼──────────────┐
                │       CryptoEngine (ABC)     │  (engine.py)
                │  ┌─────────┐ ┌────────────┐ │
                │  │ Default  │ │    KAE     │ │
                │  │ Engine   │ │   Engine   │ │
                │  │(cryptogrphy)│(ctypes+libcrypto) │
                │  └─────────┘ └────────────┘ │
                └──────────────┬──────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
    ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
    │ FileEncryptor│    │BaseProvider │    │RootKeyProvider│
    │ (encryptor.py)│   │(providers.py)│   │  (ABC)      │
    │ _aes_gcm_*() │    │ _aes_gcm_*()│   │ _hkdf_derive│
    │ → engine.*()  │   │ → engine.*() │   │ → engine.*()│
    └──────────────┘    └──────────────┘   └─────────────┘
```

### 2.2 核心变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `openviking/crypto/engine.py` | **新增** | `CryptoEngine` 抽象接口 + `DefaultCryptoEngine` + `KAECryptoEngine` |
| `openviking/crypto/providers.py` | 修改 | `RootKeyProvider` 增加 `set_engine`/`_get_engine`；`BaseProvider._aes_gcm_*` 和 `_hkdf_derive` 委托给引擎 |
| `openviking/crypto/encryptor.py` | 修改 | `FileEncryptor.__init__` 接受 `engine` 参数；`_aes_gcm_*` 委托给引擎 |
| `openviking/crypto/config.py` | 修改 | `bootstrap_encryption` 读取 `encryption.engine` 配置，创建引擎并注入 |
| `openviking/crypto/__init__.py` | 修改 | 导出新的引擎类型 |
| `openviking_cli/utils/config/encryption_config.py` | 修改 | 新增 `CryptoEngineConfig` Pydantic 模型 |

---

## 3. 详细设计

### 3.1 CryptoEngine 抽象接口

```python
class CryptoEngine(abc.ABC):
    @abc.abstractmethod
    def aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes: ...

    @abc.abstractmethod
    def aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes: ...

    @abc.abstractmethod
    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes: ...
```

**设计决策**：

- **同步接口而非异步**：AES-GCM 和 HKDF 都是 CPU 密集型操作，不涉及 I/O，用同步方法更简洁。调用方（providers/encryptor）本身是 `async def`，但内部同步调用引擎即可。
- **ciphertext 包含 auth tag**：`aes_gcm_encrypt` 返回 `ciphertext || 16-byte tag`，`aes_gcm_decrypt` 期望输入也包含 trailing tag。这与 `cryptography` 库的 `AESGCM` 行为一致。
- **无 associated_data 参数**：当前 OpenViking 的信封加密不使用 AAD（`associated_data=None`），为简化接口暂不暴露。如果未来需要 AAD，可在接口上增加可选参数。

### 3.2 DefaultCryptoEngine

```python
class DefaultCryptoEngine(CryptoEngine):
    def aes_gcm_encrypt(self, key, iv, plaintext):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).encrypt(iv, plaintext, associated_data=None)

    def aes_gcm_decrypt(self, key, iv, ciphertext):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(iv, ciphertext, associated_data=None)

    def hkdf_sha256(self, ikm, salt, info, length=32):
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        return HKDF(algorithm=SHA256(), length=length, salt=salt, info=info).derive(ikm)
```

完全等价于改造前的行为。

### 3.3 KAECryptoEngine

#### 3.3.1 系统 libcrypto 加载

```python
def _find_libcrypto() -> str:
    # 1. ctypes.util.find_library("crypto") → 动态链接器查找
    # 2. 硬编码常见路径 fallback（/usr/lib64/libcrypto.so 等）
```

使用 `ctypes.CDLL` 而非 `cffi`，原因：
- ctypes 是 Python 标准库，**零额外依赖**
- ABI 调用模式足以满足 OpenSSL C API 的需求
- 避免引入 cffi 的编译步骤

#### 3.3.2 OpenSSL 版本检测

```python
def _openssl_major_version(lib) -> int:
    ver = lib.OpenSSL_version_num()  # e.g. 0x30100010L for 3.1.0
    return (ver >> 28) & 0xF         # 提取 major version
```

#### 3.3.3 KAE 加载（双版本兼容）

| OpenSSL 版本 | API | 调用方式 |
|-------------|-----|---------|
| 1.1.1 | ENGINE API | `ENGINE_by_id("kae")` → `ENGINE_init` → `ENGINE_set_default(METHOD_ALL)` |
| 3.x | OSSL_PROVIDER API | `OSSL_PROVIDER_load(NULL, "kae")` |

**OpenSSL 1.1.1 流程**：
```c
ENGINE *e = ENGINE_by_id("kae");
ENGINE_init(e);
ENGINE_set_default(e, ENGINE_METHOD_ALL);  // 0xFFFF
```
加载后，所有 `EVP_EncryptInit_ex(ctx, cipher, NULL, ...)` 调用会自动使用 KAE 引擎。为了确保明确性，我们在 `EVP_EncryptInit_ex` 中也传入 `engine_ptr`。

**OpenSSL 3.x 流程**：
```c
OSSL_PROVIDER *prov = OSSL_PROVIDER_load(NULL, "kae");
```
加载后，KAE provider 会注册到默认 library context，后续 EVP 调用自动优先使用。

#### 3.3.4 AES-256-GCM 加密流程

```
EVP_CIPHER_CTX_new()
    │
    ├─ EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), engine, NULL, NULL)
    ├─ EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL)
    ├─ EVP_EncryptInit_ex(ctx, NULL, NULL, key, iv)
    ├─ EVP_EncryptUpdate(ctx, out, &out_len, plaintext, pt_len)
    ├─ EVP_EncryptFinal_ex(ctx, final, &final_len)
    ├─ EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag)
    │
    └─ return out[:ct_len] + tag
EVP_CIPHER_CTX_free(ctx)
```

#### 3.3.5 AES-256-GCM 解密流程

```
EVP_CIPHER_CTX_new()
    │
    ├─ EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), engine, NULL, NULL)
    ├─ EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL)
    ├─ EVP_DecryptInit_ex(ctx, NULL, NULL, key, iv)
    ├─ EVP_DecryptUpdate(ctx, out, &out_len, ct_body, ct_len)
    ├─ EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, 16, tag)
    ├─ EVP_DecryptFinal_ex(ctx, final, &final_len)  ← 验证 auth tag
    │
    └─ return out[:pt_len]
EVP_CIPHER_CTX_free(ctx)
```

#### 3.3.6 HKDF 密钥派生

通过 `EVP_PKEY_CTX` 的 HKDF 控制接口实现：

```
EVP_PKEY_CTX_new_id(EVP_PKEY_HKDF, NULL)
    │
    ├─ EVP_PKEY_derive_init(pctx)
    ├─ ctrl(HKDF_MODE, EXTRACT_AND_EXPAND)
    ├─ ctrl(HKDF_MD, EVP_sha256())
    ├─ ctrl(HKDF_SALT, salt, salt_len)
    ├─ ctrl(HKDF_KEY, ikm, ikm_len)
    ├─ ctrl(HKDF_INFO, info, info_len)
    ├─ EVP_PKEY_derive(pctx, out, &out_len)
    │
    └─ return out[:out_len]
EVP_PKEY_CTX_free(pctx)
```

使用 `EVP_PKEY_CTX_ctrl` 而非宏封装函数，因为不同 OpenSSL 版本中宏定义可能不同，直接使用 ctrl 调用更可靠。

控制码常量（来源：OpenSSL 头文件 `evp.h`）：

| 常量 | 值 | 说明 |
|------|------|------|
| `EVP_PKEY_HKDF` | 1036 | HKDF 算法 ID |
| `EVP_PKEY_OP_DERIVE` | `1 << 10` | 操作类型 |
| `EVP_PKEY_CTRL_HKDF_MD` | `0x1000 + 1` | 设置消息摘要 |
| `EVP_PKEY_CTRL_HKDF_SALT` | `0x1000 + 2` | 设置 salt |
| `EVP_PKEY_CTRL_HKDF_KEY` | `0x1000 + 3` | 设置输入密钥材料 |
| `EVP_PKEY_CTRL_HKDF_INFO` | `0x1000 + 4` | 设置 info |
| `EVP_PKEY_CTRL_HKDF_MODE` | `0x1000 + 5` | 设置 HKDF 模式 |

---

### 3.4 引擎注入流程

```
bootstrap_encryption(config)
    │
    ├─ 1. 读取 config["encryption"]["engine"]
    │     type: "default" | "kae"
    │     engine_id: "kae" (仅 type=kae 时使用)
    │
    ├─ 2. create_crypto_engine(type, **kwargs)
    │     → DefaultCryptoEngine() 或 KAECryptoEngine(engine_id="kae")
    │
    ├─ 3. create_root_key_provider(provider_type, config, engine=engine)
    │     → provider.set_engine(engine)
    │
    └─ 4. FileEncryptor(provider, engine=engine)
          → self._engine = engine
```

**关键设计**：引擎是**单例注入**的，一个 `bootstrap_encryption` 调用只创建一个引擎实例，同时注入到 Provider 和 FileEncryptor，确保整个加密链路使用相同的底层实现。

### 3.5 Provider 中引擎的使用

改造前：

```python
class BaseProvider(RootKeyProvider):
    async def _aes_gcm_encrypt(self, key, iv, plaintext):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).encrypt(iv, plaintext, associated_data=None)
```

改造后：

```python
class BaseProvider(RootKeyProvider):
    async def _aes_gcm_encrypt(self, key, iv, plaintext):
        return self._get_engine().aes_gcm_encrypt(key, iv, plaintext)
```

`_get_engine()` 有懒加载机制：如果没有通过 `set_engine()` 注入引擎，则自动创建 `DefaultCryptoEngine()`，保证向后兼容。

### 3.6 配置模型

```python
class CryptoEngineConfig(BaseModel):
    type: str = "default"      # "default" | "kae"
    engine_id: str = "kae"     # OpenSSL ENGINE/PROVIDER 名称

class EncryptionConfig(BaseModel):
    enabled: bool = False
    provider: str = "local"
    engine: CryptoEngineConfig = CryptoEngineConfig()  # 新增
    local: LocalEncryptionProviderConfig = ...
    vault: VaultEncryptionProviderConfig = ...
    volcengine_kms: VolcengineKMSEncryptionProviderConfig = ...
```

---

## 4. 配置示例

### 4.1 默认引擎（向后兼容，不需要配置 engine）

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}
```

### 4.2 KAE 引擎

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "engine": {
      "type": "kae",
      "engine_id": "kae"
    },
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}
```

### 4.3 KAE 引擎 + Vault Provider

```json
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "engine": {
      "type": "kae",
      "engine_id": "kae"
    },
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "hvs.xxxxx"
    }
  }
}
```

### 4.4 使用 uadk_engine（OpenSSL 3.x 的 UADK Provider）

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "engine": {
      "type": "kae",
      "engine_id": "uadk_provider"
    },
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}
```

---

## 5. 向后兼容性

| 场景 | 行为 |
|------|------|
| 不配置 `encryption.engine` | 使用 `DefaultCryptoEngine`，等价于改造前 |
| `encryption.engine.type = "default"` | 同上 |
| `encryption.engine.type = "kae"` | 使用 `KAECryptoEngine`，加载系统 OpenSSL + KAE |
| 现有测试 | `FileEncryptor(provider)` 和 `LocalFileProvider(path)` 签名不变，引擎参数可选 |
| 现有加密文件 | 信封格式（OVE1）不变，KAE 引擎产出的密文与 Default 引擎**互相可解密**（同一算法 AES-256-GCM） |
| 没有安装 KAE 的环境 | 配置 `type: "kae"` 时启动报错，给出明确提示 |

---

## 6. 错误处理

| 错误场景 | 引擎 | 异常类型 | 错误信息 |
|---------|------|---------|---------|
| 找不到系统 libcrypto | KAE | `ConfigError` | "Cannot locate system libcrypto. Install openssl-devel / libssl-dev" |
| KAE ENGINE 未找到 (1.1.1) | KAE | `ConfigError` | "KAE engine 'kae' not found. Ensure libkae is installed" |
| KAE PROVIDER 未找到 (3.x) | KAE | `ConfigError` | "KAE provider 'kae' not available. Ensure kae provider is installed" |
| KAE ENGINE 初始化失败 | KAE | `ConfigError` | "Failed to initialise KAE engine 'kae'" |
| GCM 认证失败 | 两者 | `AuthenticationFailedError` | "GCM authentication failed" / "Decryption failed: ..." |
| 未知引擎类型 | — | `ConfigError` | "Unknown crypto engine 'xxx'. Supported: default, kae" |

---

## 7. 性能考量

| 方面 | Default 引擎 | KAE 引擎 |
|------|-------------|---------|
| OpenSSL 链接方式 | 静态链接（cryptography wheel 内嵌） | 动态链接（系统 libcrypto.so） |
| 硬件加速 | 无（纯软件 AES-NI 可能可用，取决于 wheel 编译选项） | KAE 硬件加速（鲲鹏 920 加速引擎） |
| 调用开销 | Python → C extension → OpenSSL | Python → ctypes → libcrypto（略高于 C extension） |
| 适用场景 | 通用环境、开发环境 | 鲲鹏服务器、高吞吐生产环境 |

ctypes 调用开销约为每次函数调用 ~1μs，对于 AES-GCM 加密一个文件（通常 KB~MB 级别）可忽略不计。真正的性能提升来自 KAE 硬件加速的 AES 运算。

---

## 8. 依赖关系

| 引擎 | Python 依赖 | 系统依赖 |
|------|------------|---------|
| default | `cryptography>=42.0.0` | 无（静态链接 OpenSSL） |
| kae | 无额外 Python 包（仅 ctypes 标准库） | `libcrypto.so`（OpenSSL 1.1.1 或 3.x）+ KAE 引擎/Provider |

---

## 9. 测试策略

| 测试层级 | 覆盖内容 | 说明 |
|---------|---------|------|
| 现有单测 | `test_encryptor.py`, `test_local_provider.py` | 不改变测试代码，默认使用 DefaultCryptoEngine，验证向后兼容 |
| 新增单测（建议） | `test_engine.py` — DefaultCryptoEngine 的 encrypt/decrypt/hkdf 正确性 | 不依赖 KAE 硬件，CI 可运行 |
| 集成测试（建议） | KAECryptoEngine 在有 KAE 的环境上 encrypt/decrypt roundtrip | 需要鲲鹏服务器 |
| 交叉兼容测试（建议） | Default 引擎加密 → KAE 引擎解密，反之亦然 | 验证两个引擎互操作 |

---

## 10. 未来扩展

1. **国密算法（SM4-GCM）**：在 `CryptoEngine` 中增加 `sm4_gcm_encrypt`/`sm4_gcm_decrypt` 方法，或通过配置参数选择算法
2. **更多硬件加速器**：只需实现 `CryptoEngine` 接口并注册到 `_ENGINES` 字典
3. **AAD 支持**：在接口中增加 `associated_data` 可选参数
4. **异步引擎**：如果需要调用远程 HSM，可增加 `AsyncCryptoEngine` 子类
5. **性能指标**：在引擎层增加加密耗时/吞吐量 metrics

---

## 11. 文件级变更详情

### 11.1 `openviking/crypto/engine.py`（新增，约 280 行）

- `CryptoEngine`（ABC）：3 个抽象方法
- `DefaultCryptoEngine`：3 个方法，直接调用 `cryptography` 库
- `KAECryptoEngine`：
  - `__init__`：加载 libcrypto、检测版本、设置 ctypes 签名、加载 KAE
  - `_setup_ctypes_signatures`：为 30+ OpenSSL C 函数声明 `restype`/`argtypes`
  - `_load_kae`：根据版本调用 ENGINE 或 PROVIDER API
  - `aes_gcm_encrypt`：完整 EVP 加密流程
  - `aes_gcm_decrypt`：完整 EVP 解密流程（含 tag 验证）
  - `hkdf_sha256`：EVP_PKEY_CTX HKDF 流程
- `create_crypto_engine`：工厂函数

### 11.2 `openviking/crypto/providers.py`（修改）

- `RootKeyProvider`：增加 `set_engine`/`_get_engine`（懒加载 Default 引擎）
- `BaseProvider._aes_gcm_encrypt/decrypt`：从内联 `cryptography` 调用改为 `self._get_engine().xxx()`
- `RootKeyProvider._hkdf_derive`：从内联 `cryptography` 调用改为 `self._get_engine().hkdf_sha256()`
- `create_root_key_provider`：增加 `engine` 参数，创建后调用 `provider.set_engine(engine)`

### 11.3 `openviking/crypto/encryptor.py`（修改）

- `FileEncryptor.__init__`：增加 `engine` 参数（默认 None → DefaultCryptoEngine）
- `FileEncryptor._aes_gcm_encrypt/decrypt`：从内联 `cryptography` 调用改为 `self._engine.xxx()`

### 11.4 `openviking/crypto/config.py`（修改）

- `bootstrap_encryption`：读取 `encryption.engine` 配置，调用 `create_crypto_engine`，注入到 provider 和 encryptor

### 11.5 `openviking_cli/utils/config/encryption_config.py`（修改）

- 新增 `CryptoEngineConfig` Pydantic 模型
- `EncryptionConfig` 增加 `engine: CryptoEngineConfig` 字段
