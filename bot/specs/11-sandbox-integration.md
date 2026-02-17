# vikingbot 沙箱集成技术规范

## 1. 概述

为 vikingbot 添加基于 `@anthropic-ai/sandbox-runtime` 的沙箱支持，为每个 session 提供独立的文件系统和网络隔离环境。

## 2. 架构设计

### 2.1 模块结构

```
vikingbot/
├── sandbox/                    # 新增模块
│   ├── __init__.py
│   ├── manager.py              # 沙箱生命周期管理（统一入口）
│   ├── config.py               # 沙箱配置 Schema
│   ├── base.py                # 沙箱抽象接口
│   ├── backends/              # 沙箱后端实现
│   │   ├── __init__.py
│   │   ├── srt.py            # @anthropic-ai/sandbox-runtime 后端
│   │   ├── docker.py         # Docker 后端（未来）
│   │   └── firecracker.py    # Firecracker 后端（未来）
```

### 2.2 集成点

| 组件 | 集成方式 |
|------|---------|
| `config/schema.py` | 添加 `SandboxConfig` |
| `session/manager.py` | 每个关联一个沙箱实例 |
| `agent/tools/shell.py` | 通过沙箱执行命令 |
| `agent/tools/filesystem.py` | 通过沙箱进行文件操作 |

### 2.3 扩展设计原则

- **开闭原则**：新增沙箱后端无需修改核心代码
- **插件化**：每个后端是独立的模块，通过配置选择
- **统一接口**：所有后端实现相同的抽象接口

## 3. 配置设计

### 3.1 配置结构

```json
{
  "sandbox": {
    "enabled": false,                    // 全局开关
    "backend": "srt",                   // 沙箱后端：srt | docker | firecracker
    "mode": "per-session",               // "per-session" | "shared" | "disabled"
    "network": {
      "allowedDomains": [],               // 允许的域名
      "deniedDomains": [],               // 禁止的域名
      "allowLocalBinding": false
    },
    "filesystem": {
      "denyRead": ["~/.ssh", "~/.gnupg"], // 禁止读取的路径
      "allowWrite": ["~/.vikingbot/workspace"], // 允许写入的路径
      "denyWrite": [".env", "*.pem"]    // 禁止写入的文件模式
    },
    "runtime": {
      "cleanupOnExit": true,          // 退出时清理沙箱
      "timeout": 300                  // 沙箱进程超时（秒）
    },
    "backends": {
      "srt": {
        "settingsPath": "~/.vikingbot/srt-settings.json"
      },
      "docker": {
        "image": "python:3.11-slim",
        "networkMode": "bridge"
      }
    }
  }
}
```

### 3.2 配置 Schema (Pydantic)

```python
class SandboxNetworkConfig(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    allow_local_binding: bool = False

class SandboxFilesystemConfig(BaseModel):
    deny_read: list[str] = Field(default_factory=list)
    allow_write: list[str] = Field(default_factory=list)
    deny_write: list[str] = Field(default_factory=list)

class SandboxRuntimeConfig(BaseModel):
    cleanup_on_exit: bool = True
    timeout: int = 300

class SrtBackendConfig(BaseModel):
    settings_path: str = "~/.vikingbot/srt-settings.json"

class DockerBackendConfig(BaseModel):
    image: str = "python:3.11-slim"
    network_mode: str = "bridge"

class SandboxBackendsConfig(BaseModel):
    srt: SrtBackendConfig = Field(default_factory=SrtBackendConfig)
    docker: DockerBackendConfig = Field(default_factory=DockerBackendConfig)

class SandboxConfig(BaseModel):
    enabled: bool = False
    backend: str = "srt"  # 后端类型
    mode: Literal["per-session", "shared", "disabled"] = "disabled"
    network: SandboxNetworkConfig = Field(default_factory=SandboxNetworkConfig)
    filesystem: SandboxFilesystemConfig = Field(default_factory=SandboxFilesystemConfig)
    runtime: SandboxRuntimeConfig = Field(default_factory=SandboxRuntimeConfig)
    backends: SandboxBackendsConfig = Field(default_factory=SandboxBackendsConfig)
```

## 4. 核心组件设计

### 4.1 抽象接口 (sandbox/base.py)

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

class SandboxBackend(ABC):
    """
    沙箱后端抽象接口。

    所有沙箱后端必须实现此接口。
    """

    @abstractmethod
    async def start(self) -> None:
        """启动沙箱实例。"""
        pass

    @abstractmethod
    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """
        在沙箱中执行命令。

        Args:
            command: 要执行的命令
            timeout: 超时时间（秒）
            **kwargs: 后端特定参数

        Returns:
            命令输出（stdout + stderr）
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止沙箱实例并清理资源。"""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """检查沙箱是否正在运行。"""
        pass

    @property
    @abstractmethod
    def workspace(self) -> Path:
        """获取沙箱工作目录。"""
        pass
```

### 4.2 后端注册机制 (sandbox/backends/__init__.py)

```python
from typing import Type, Dict
from vikingbot.sandbox.base import SandboxBackend

# 后端注册表
_BACKENDS: Dict[str, Type[SandboxBackend]] = {}


def register_backend(name: str) -> callable:
    """
    装饰器，用于注册沙箱后端。

    使用方式：
        @register_backend("srt")
        class SrtBackend(SandboxBackend):
            ...
    """

    def decorator(cls: Type[SandboxBackend]) -> Type[SandboxBackend]:
        _BACKENDS[name] = cls
        return cls

    return decorator


def get_backend(name: str) -> Type[SandboxBackend] | None:
    """根据名称获取后端类。"""
    return _BACKENDS.get(name)


def list_backends() -> list[str]:
    """列出所有已注册的后端。"""
    return list(_BACKENDS.keys())


# 导入后端实现以触发注册
from vikingbot.sandbox.backends.srt import SrtBackend
# from vikingbot.sandbox.backends.docker import DockerBackend  # 未来
```

### 4.3 SandboxManager (sandbox/manager.py)

```python
from vikingbot.sandbox.backends import get_backend
from vikingbot.sandbox.base import SandboxBackend


class SandboxManager:
    """
    沙箱管理器，负责创建和管理沙箱实例。

    支持多种后端实现（SRT、Docker、Firecracker 等）。
    """

    def __init__(self, config: SandboxConfig, workspace: Path):
        self.config = config
        self.workspace = workspace
        self._sandboxes: dict[str, SandboxBackend] = {}
        self._shared_sandbox: SandboxBackend | None = None

        # 获取后端类
        backend_cls = get_backend(config.backend)
        if not backend_cls:
            raise UnsupportedBackendError(f"Unknown sandbox backend: {config.backend}")
        self._backend_cls = backend_cls

    async def get_sandbox(self, session_key: str) -> SandboxBackend:
        """根据配置模式获取沙箱实例。"""
        if not self.config.enabled:
            raise SandboxDisabledError()

        if self.config.mode == "per-session":
            return await self._get_or_create_session_sandbox(session_key)
        elif self.config.mode == "shared":
            return await self._get_or_create_shared_sandbox()
        else:
            raise SandboxDisabledError()

    async def _get_or_create_session_sandbox(self, session_key: str) -> SandboxBackend:
        """获取或创建 session 专属沙箱。"""
        if session_key not in self._sandboxes:
            sandbox = await self._create_sandbox(session_key)
            self._sandboxes[session_key] = sandbox
        return self._sandboxes[session_key]

    async def _get_or_create_shared_sandbox(self) -> SandboxBackend:
        """获取或创建共享沙箱。"""
        if self._shared_sandbox is None:
            self._shared_sandbox = await self._create_sandbox("shared")
        return self._shared_sandbox

    async def _create_sandbox(self, session_key: str) -> SandboxBackend:
        """创建新的沙箱实例。"""
        workspace = self.workspace / session_key.replace(":", "_")
        instance = self._backend_cls(self.config, session_key, workspace)
        await instance.start()
        await self._copy_bootstrap_files(workspace)
        return instance

    async def _copy_bootstrap_files(self, sandbox_workspace: Path) -> None:
        """复制初始化文件到沙箱工作目录。"""
        from vikingbot.agent.context import ContextBuilder
        import shutil

        init_dir = self.workspace / ContextBuilder.INIT_DIR
        if init_dir.exists() and init_dir.is_dir():
            for item in init_dir.iterdir():
                src = init_dir / item.name
                dst = sandbox_workspace / item.name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

        skills_dir = self.workspace / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            dst_skills = sandbox_workspace / "skills"
            shutil.copytree(skills_dir, dst_skills, dirs_exist_ok=True)

        if not init_dir.exists():
            bootstrap_files = ContextBuilder.BOOTSTRAP_FILES
            for filename in bootstrap_files:
                src = self.workspace / filename
                if src.exists():
                    dst = sandbox_workspace / filename
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    async def cleanup_session(self, session_key: str) -> None:
        """清理 session 对应的沙箱。"""
        if session_key in self._sandboxes:
            await self._sandboxes[session_key].stop()
            del self._sandboxes[session_key]

    async def cleanup_all(self) -> None:
        """清理所有沙箱。"""
        for sandbox in self._sandboxes.values():
            await sandbox.stop()
        self._sandboxes.clear()


()

if self._shared_sandbox:
    await self._shared_sandbox.stop()
    self._shared_sandbox = None
```

### 4.4 SRT 后端实现 (sandbox/backends/srt.py)

```python
import asyncio
import json
from pathlib import Path
from vikingbot.sandbox.base import SandboxBackend


@register_backend("srt")
class SrtBackend(SandboxBackend):
    """
    @anthropic-ai/sandbox-runtime 后端实现。
    """

    def __init__(self, config: SandboxConfig, session_key: str, workspace: Path):
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._process: asyncio.subprocess.Process | None = None
        self._settings_path = self._generate_settings()

    def _generate_settings(self) -> Path:
        """生成 SRT 配置文件。"""
        srt_config = {
            "network": {
                "allowedDomains": self.config.network.allowed_domains,
                "deniedDomains": self.config.network.denied_domains,
                "allowLocalBinding": self.config.network.allow_local_binding
            },
            "filesystem": {
                "denyRead": self.config.filesystem.deny_read,
                "allowWrite": self.config.filesystem.allow_write,
                "denyWrite": self.config.filesystem.deny_write
            }
        }

        settings_path = Path.home() / ".vikingbot" / "sandboxes" / f"{self.session_key.replace(':', '_')}-srt-settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        with open(settings_path, "w") as f:
            json.dump(srt_config, f, indent=2)

        return settings_path

    async def start(self) -> None:
        """启动 SRT 沙箱进程。"""
        self._workspace.mkdir(parents=True, exist_ok=True)

        # 启动 SRT 包装器
        cmd = [
            "node",
            "-e",
            self._get_wrapper_script(),
            "--settings", str(self._settings_path),
            "--workspace", str(self._workspace)
        ]

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

    async def execute(self, command: str, timeout: int = 60, **kwargs) -> str:
        """在沙箱中执行命令。"""
        if not self._process:
            raise SandboxNotStartedError()

        # 通过 IPC 发送命令到沙箱进程
        # 沙箱进程使用 SRT 包装命令执行
        # TODO: 实现 IPC 通信
        pass

    async def stop(self) -> None:
        """停止沙箱进程。"""
        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None

    def is_running(self) -> bool:
        """检查沙箱是否正在运行。"""
        return self._process is not None and self._process.returncode is None

    @property
    def workspace(self) -> Path:
        """获取沙箱工作目录。"""
        return self._workspace

    def _get_wrapper_script(self) -> str:
        """获取 Node.js 包装脚本。"""
        return """
        const { SandboxManager } = require('@anthropic-ai/sandbox-runtime');

        async function main() {
            const config = require(process.argv[2]);
            await SandboxManager.initialize(config);

            // 启动 IPC 服务器接收命令
            // ...
        }

        main().catch(console.error);
        """
```

### 4.5 Docker 后端示例 (sandbox/backends/docker.py) - 未来实现

```python
@register_backend("docker")
class DockerBackend(SandboxBackend):
    """
    Docker 沙箱后端实现（示例）。

    展示如何添加新的沙箱后端。
    """

    def __init__(self, config: SandboxConfig, session_key: str, workspace: Path):
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._container_id: str | None = None

    async def start(self) -> None:
        """启动 Docker 容器。"""
        # 使用 docker SDK 启动容器
        pass

    async def execute(self, command: str, timeout: int = 60, **kwargs) -> str:
        """在容器中执行命令。"""
        # 使用 docker exec 执行命令
        pass

    async def stop(self) -> None:
        """停止并删除容器。"""
        # 停止并删除容器
        pass

    def is_running(self) -> bool:
        """检查容器是否正在运行。"""
        # 检查容器状态
        pass

    @property
    def workspace(self) -> Path:
        """获取容器工作目录。"""
        return self._workspace
```

## 5. 工具集成

### 5.1 路径处理策略

当沙箱启用时，工具对路径的处理分为两种策略：

#### 5.1.1 文件系统工具（ReadFileTool、WriteFileTool、EditFileTool、ListDirTool）

这类工具会将路径映射到沙箱工作区：

| 输入路径 | 处理结果 | 示例 |
|---------|---------|------|
| **相对路径** | 直接拼接到沙箱工作区 | `test.txt` → `sandbox.workspace / "test.txt"` |
| **绝对路径** | 去除前导斜杠后拼接到沙箱工作区 | `/test.txt` → `sandbox.workspace / "test.txt"` |
| **根路径 `/`** | 直接映射到沙箱工作区 | `/` → `sandbox.workspace` |

**示例**：
```python
# 沙箱工作区为 /tmp/sandbox/session1
read_file("/test.txt")    # 实际读取 /tmp/sandbox/session1/test.txt
read_file("test.txt")     # 实际读取 /tmp/sandbox/session1/test.txt
list_dir("/")             # 实际列出 /tmp/sandbox/session1/
```

#### 5.1.2 Shell 执行工具（ExecTool）

这类工具**不做路径转换**，直接将命令传递给沙箱后端执行：

| 输入 | 处理结果 |
|-----|---------|
| 命令字符串 | 直接传递给 SRT 沙箱，由沙箱本身处理路径 |
| `pwd` 命令 | 特殊处理，直接返回 `/` |

**注意**：ExecTool 与文件系统工具的路径处理策略**不一致**。ExecTool 完全依赖 SRT 沙箱的路径处理能力。

#### 5.1.3 删除文件操作

**当前实现**：没有专门的删除文件工具，删除文件通过 ExecTool 执行 `rm` 命令完成。

| 删除场景 | 示例命令 | 结果行为 |
|---------|---------|---------|
| **删除沙箱内相对路径文件** | `rm test.txt` | ✅ 由 SRT 沙箱在其工作区执行，删除沙箱内的文件 |
| **删除沙箱内绝对路径文件** | `rm /test.txt` | ✅ 由 SRT 沙箱在其工作区执行，删除沙箱内的文件 |
| **递归删除沙箱内目录** | `rm -r subdir` | ✅ **沙箱模式下允许**（本地安全守卫被跳过） |
| **强制递归删除** | `rm -rf subdir` | ✅ **沙箱模式下允许**（本地安全守卫被跳过） |
| **删除沙箱外绝对路径文件** | `rm /etc/passwd` | 🛡️ 由 SRT 沙箱安全规则拦截，根据 `denyRead`/`denyWrite` 配置决定是否允许 |

**安全策略说明**：
- **沙箱启用时**：跳过本地安全守卫 `_guard_command()`，完全依赖 SRT 沙箱的隔离能力
  - 沙箱内的文件用户有完全控制权，允许 `rm -r`/`rm -rf` 等操作
  - **重要**：SRT 后端会**动态将沙箱工作目录的绝对路径添加到 `allowWrite`** 中
  - 默认 `filesystem.allowWrite` 为空列表，无需手动配置
  - 只能写入/删除沙箱工作目录及其子目录，沙箱外的访问会被阻止（返回 Operation not permitted）
- **沙箱禁用时**：应用本地安全守卫，阻止 `rm -rf` 等危险模式

### 5.2 修改 ExecTool

```python
class ExecTool(Tool):
    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        sandbox_manager: SandboxManager | None = None,  # 新增
        session_key: str | None = None,                # 新增
        # ... 其他参数
    ):
        self.sandbox_manager = sandbox_manager
        self.session_key = session_key
        # ... 其他初始化

    async def execute(self, command: str, working_dir: str | None = None, **kwargs) -> str:
        # 如果启用了沙箱，通过沙箱执行
        if self.sandbox_manager and self.session_key and self.sandbox_manager.config.enabled:
            sandbox = await self.sandbox_manager.get_sandbox(self.session_key)
            
            # pwd 命令特殊处理
            if command.strip() == "pwd":
                return "/"
            
            # 其他命令直接传递给沙箱（不做路径转换）
            return await sandbox.execute(command, timeout=self.timeout)

        # 否则直接执行（原有逻辑）
        # ...
```

### 5.3 修改文件系统工具

以 ReadFileTool 为例：

```python
class ReadFileTool(Tool):
    async def execute(self, path: str, **kwargs: Any) -> str:
        if self._sandbox_manager and self._session_key and self._sandbox_manager.config.enabled:
            sandbox = await self._sandbox_manager.get_sandbox(self._session_key)
            input_path = Path(path)

            if input_path.is_absolute():
                # 绝对路径：去除前导斜杠后拼接到沙箱工作区
                if path == "/":
                    sandbox_path = sandbox.workspace
                else:
                    sandbox_path = sandbox.workspace / path.lstrip("/")
            else:
                # 相对路径：直接拼接到沙箱工作区
                sandbox_path = sandbox.workspace / path
            
            # 读取沙箱中的文件
            content = sandbox_path.read_text(encoding="utf-8")
            return content
        
        # 原有逻辑...
```

## 6. 生命周期管理

### 6.1 Session 创建时

```python
# session/manager.py
def get_or_create(self, key: str) -> Session:
    session = self._load(key) or Session(key=key)

    # 如果启用了沙箱，为 session 创建沙箱
    if self.sandbox_manager and self.sandbox_manager.config.enabled:
        asyncio.create_task(
            self.sandbox_manager.get_sandbox(key)
        )

    return session
```

### 6.2 Session 销毁时

```python
# session/manager.py
async def delete(self, key: str) -> bool:
    # 清理关联的沙箱
    if self.sandbox_manager:
        await self.sandbox_manager.cleanup_session(key)

    # ... 原有逻辑
```

## 7. 错误处理

```python
class SandboxError(Exception):
    """沙箱基础异常。"""
    pass

class SandboxNotStartedError(SandboxError):
    """沙箱未启动。"""
    pass

class SandboxDisabledError(SandboxError):
    """沙箱功能未启用。"""
    pass

class SandboxExecutionError(SandboxError):
    """沙箱命令执行失败。"""
    pass

class UnsupportedBackendError(SandboxError):
    """不支持的沙箱后端。"""
    pass
```

## 8. 依赖管理

### 8.1 新增依赖

```toml
[project.dependencies]
# ... 现有依赖
# Node.js 包，需要通过 npm 安装
```

### 8.2 安装脚本

```bash
# scripts/install-sandbox.sh
#!/bin/bash
# 检查 Node.js 是否安装
if ! command -v node &> /dev/null; then
    echo "Error: Node.js is required for sandbox support"
    exit 1
fi

# 安装 sandbox-runtime
npm install -g @anthropic-ai/sandbox-runtime
```

## 9. 测试策略

### 9.1 单元测试

- `test_sandbox_sconfig.py` - 配置解析测试
- `test_sandbox_manager.py` - 沙箱管理器测试
- `test_sandbox_backends.py` - 各后端测试

### 9.2 集成测试

- `test_sandbox_integration.py` - 端到端沙箱功能测试

## 10. 文档更新

### 10.1 README.md

添加 "沙盒安全" 章节：

```markdown
## 沙盒安全

vikingbot 支持多种沙盒后端，为每个会话提供独立的文件系统和网络限制。

### 支持的后端

- **SRT** (@anthropic-ai/sandbox-runtime): 轻量级沙盒，无需容器
- **Docker**: 基于 Docker 容器的沙盒（未来）
- **Firecracker**: 基于 Firecracker 微虚拟机的沙盒（未来）

### 启用沙盒

1. 安装依赖：
```bash
npm install -g @anthropic-ai/sandbox-runtime
```

2. 配置 `~/.vikingbot/config.json`：
```json
{
  "sandbox": {
    "enabled": true,
    "backend": "srt",
    "mode": "per-session",
    "filesystem": {
      "allowWrite": ["~/.vikingbot/workspace"],
      "denyRead": ["~/.ssh"]
    },
    "network": {
      "allowedDomains": ["api.openai.com"]
    }
  }
}
```

### 配置模式

- `per-session`: 每个会话独立沙盒（推荐）
- `shared`: 所有会话共享一个沙盒
- `disabled`: 禁用沙盒
```

## 11. 实现优先级

| 优先级 | 任务 | 说明 | 状态 |
|--------|------|------|------|
| P0 | 配置 Schema | 添加 `SandboxConfig` 到 `config/schema.py` | ✅ 已完成 |
| P0 | 抽象接口 | 实现 `SandboxBackend` 基类和注册后端机制 | ✅ 已完成 |
| P0 | 沙箱管理器 | 实现 `SandboxManager` | ✅ 已完成 |
| P1 | SRT 后端 | 实现 `SrtBackend` | ✅ 已完成 |
| P1 | Shell 工具集成 | 修改 `ExecTool` 支持沙箱执行 | ✅ 已完成 |
| P1 | Session 集成 | 在 `SessionManager` 中集成沙箱生命周期 | ✅ 已完成 |
| P1 | 文件系统工具集成 | 修改 `ReadFileTool`/`WriteFileTool`/`EditFileTool`/`ListDirTool` 支持沙箱 | ✅ 已完成 |
| P1 | 单元测试 | 添加沙箱工具路径处理测试 | ✅ 已完成 |
| P2 | 安装脚本 | 添加依赖安装脚本 | ⏳ 待完成 |
| P3 | Docker 后端 | 实现 `DockerBackend`（可选） | ⏳ 待完成 |
| P3 | 文档更新 | 更新 README 和配置示例 | ⏳ 待完成 |
