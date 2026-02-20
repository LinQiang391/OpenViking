# 沙箱后端配置指南

## 概述

vikingbot 支持多种沙箱后端，为不同场景提供灵活的选择。

## 沙箱模式

### per-session
- 每个会话独立工作区
- 会话之间完全隔离
- 适用于需要隔离的场景

### shared
- 所有会话共享同一个工作区
- 记忆和技能在会话间共享
- 适用于协作或需要共享文件的场景

## 后端类型

### Direct（推荐用于开发和调试）
- 直接在宿主机执行命令和文件操作
- 无隔离，最快
- 适合本地开发使用

### SRT（基于 @anthropic-ai/sandbox-runtime）
- 轻量级沙箱，无需容器
- 文件系统和网络隔离
- 需要 Node.js 环境

### OpenSandbox（基于 Alibaba OpenSandbox）
- 支持本地 Docker 环境
- 支持 VKE（火山引擎 Kubernetes）环境
- TOS 共享目录挂载
- 适合生产环境使用

### AIO Sandbox（基于 agent-sandbox SDK）
- 基于 agent-sandbox SDK
- 连接到外部沙箱服务
- 主目录可配置

## 配置示例

### 默认配置

```json
{
  "sandbox": {
    "backend": "direct",
    "mode": "shared"
  }
}
```

### SRT 配置

```json
{
  "sandbox": {
    "backend": "srt",
    "mode": "per-session",
    "backends": {
      "srt": {
        "node_path": "node"
      }
    },
    "filesystem": {
      "deny_read": [],
      "allow_write": [],
      "deny_write": []
    }
  }
}
```

### OpenSandbox 配置

```json
{
  "sandbox": {
    "backend": "opensandbox",
    "mode": "per-session",
    "backends": {
      "opensandbox": {
        "server_url": "http://localhost:8080",
        "api_key": "",
        "default_image": "opensandbox/code-interpreter:v1.0.1"
      }
    }
  }
}
```

### AIO Sandbox 配置

```json
{
  "sandbox": {
    "backend": "aiosandbox",
    "mode": "shared",
    "backends": {
      "aiosandbox": {
        "base_url": "http://localhost:18794"
      }
    }
  }
}
```

## 工作区路径

- per-session 模式：`~/.vikingbot/workspace/{session-key}`
- shared 模式：`~/.vikingbot/workspace/shared`

## OpenSandbox 环境自动检测

OpenSandbox 后端会自动检测运行环境：

| 环境 | 检测方式 | 配置来源 |
|------|---------|---------|
| **本地** | 无 Kubernetes 环境变量 / 无 kubeconfig | `opensandbox.local` 配置 |
| **VKE** | 检测到 `KUBERNETES_SERVICE_HOST` 或存在 `/var/run/secrets/kubernetes.io/serviceaccount` | `opensandbox.vke` 配置 |

## 架构设计

### 模块结构

```
vikingbot/
├── sandbox/
│   ├── __init__.py
│   ├── manager.py              # 沙箱生命周期管理
│   ├── base.py                # 沙箱抽象接口
│   └── backends/              # 沙箱后端实现
│       ├── srt.py
│       ├── direct.py
│       ├── opensandbox.py
│       └── aiosandbox.py
```

### 扩展设计原则

- **开闭原则**：新增沙箱后端无需修改核心代码
- **插件化**：每个后端是独立的模块，通过配置选择
- **统一接口**：所有后端实现相同的抽象接口

## 后端选择建议

| 场景 | 推荐后端 | 推荐模式
|------|------------|----------
| 本地开发 | Direct | shared
| 需要隔离 | SRT | per-session
| 生产环境 | OpenSandbox | per-session 或 shared
