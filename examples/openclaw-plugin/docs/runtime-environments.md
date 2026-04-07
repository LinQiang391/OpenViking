# OpenClaw 双环境运行指南

本文档说明项目中两套 OpenClaw 测试环境的配置差异与启动方式。

---

## 环境概览

| 对比项 | 默认环境 (default) | 第二环境 (second) |
|--------|-------------------|-------------------|
| Profile | 无（默认） | `--profile second` |
| 状态目录 | `~/.openclaw/` | `~/.openclaw-second/` |
| 配置文件 | `~/.openclaw/openclaw.json` | `~/.openclaw-second/openclaw.json` |
| Gateway 端口 | 18789（默认） | 18890 |
| OpenViking 插件 | **已安装**（`contextEngine` 槽位） | **未安装** |
| `contextEngine` 槽位 | `openviking` | `legacy`（默认值） |
| `memory` 槽位 | `none`（已禁用） | `memory-core`（默认值） |
| 日志路径 | `~/.openclaw/logs/` | `~/.openclaw-second/logs/openclaw-profile-second.log` |

---

## 插件槽位机制

OpenClaw 的插件系统有两个独立槽位（见 `openclaw/src/plugins/slots.ts`）：

| 槽位 Key | 对应插件 Kind | 默认插件 ID |
|-----------|--------------|-------------|
| `memory` | `memory` | `memory-core` |
| `contextEngine` | `context-engine` | `legacy` |

- **OpenViking** 的 kind 是 `context-engine`，占据 `contextEngine` 槽位，**不影响** `memory` 槽位。
- 默认环境已在 `openclaw.json` 中显式关闭了 `memory-core`（`plugins.slots.memory = "none"` 且 `plugins.entries.memory-core.enabled = false`），**仅运行 OpenViking**。
- second 环境未配置 `plugins` 节，`memory` 槽位保持默认值 `memory-core`，因此 **`memory-core` 照常启用**。
- `contextEngine` 槽位：默认环境用 `openviking`，second 环境用 `legacy`（默认值）。

---

## 环境 1：默认环境（接入 OpenViking）

### 启动方式

```powershell
# 标准启动（使用默认 profile）
openclaw gateway
```

或者直接 `openclaw gateway --port 18789`。

### 配置要点

- 状态目录：`%USERPROFILE%\.openclaw\`
- 配置文件：`%USERPROFILE%\.openclaw\openclaw.json`
- OpenViking 插件已安装到 `~/.openclaw/extensions/openviking/`，`openclaw.json` 中 `plugins.slots.contextEngine` 设为 `"openviking"`
- **`memory-core` 已关闭**：`plugins.slots.memory = "none"` 且 `plugins.entries.memory-core.enabled = false`，仅运行 OpenViking
- OpenViking 当前以 `remote` 模式运行，连接 `http://127.0.0.1:1933`
- OpenViking 配置路径：`~/.openviking/ov.conf`

### OpenViking 插件配置摘要

在 `openclaw.json` 的 `plugins.entries.openviking.config` 中可配置（均有合理默认值）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `mode` | `"local"` | `local` = 插件自启 OV server；`remote` = 连接已有 HTTP 服务 |
| `port` | `1933` | local 模式下 OV server 端口 |
| `baseUrl` | `http://127.0.0.1:1933` | remote 模式下的 OV 服务地址 |
| `autoCapture` | `true` | 自动捕获对话内容到长期记忆 |
| `autoRecall` | `true` | 自动从长期记忆中召回相关内容 |
| `recallLimit` | `6` | 每次召回的最大记忆条数 |
| `recallTokenBudget` | `2000` | 召回内容的 token 预算 |

---

## 环境 2：second 环境（未接入 OpenViking）

### 启动方式

**推荐方式（使用 `--profile second`）：**

```powershell
openclaw --profile second gateway --port 18890
```

`--profile second` 会自动设置：
- `OPENCLAW_PROFILE=second`
- `OPENCLAW_STATE_DIR=~/.openclaw-second`
- `OPENCLAW_CONFIG_PATH=~/.openclaw-second/openclaw.json`

**使用项目提供的辅助脚本（带 cache-trace 捕获）：**

```powershell
# 首次运行前，先初始化日志路径
.\ai_toolbox\openclaw_capture_context_tool\scripts\profile-second\Initialize-ProfileSecond-Capture.ps1

# 启动 gateway（包含 cache-trace 和 OpenViking 诊断日志写入）
.\ai_toolbox\openclaw_capture_context_tool\scripts\profile-second\Start-ProfileSecond-Gateway-Capture.ps1

# 可选：启动 context capture Web UI（端口 9002）
.\ai_toolbox\openclaw_capture_context_tool\scripts\profile-second\Start-ProfileSecond-CaptureWebUI.ps1
```

### 辅助脚本说明

| 脚本 | 作用 |
|------|------|
| `Initialize-ProfileSecond-Capture.ps1` | 一次性初始化：在 `~/.openclaw-second/openclaw.json` 写入 `logging.file` 配置，创建日志目录 |
| `Start-ProfileSecond-Gateway-Capture.ps1` | 启动 second gateway（端口 18890），同时启用 `OPENCLAW_CACHE_TRACE` 系列环境变量，trace 数据写入 `ai_toolbox/.../data/context_capture_profile_second/` |
| `Start-ProfileSecond-CaptureWebUI.ps1` | 启动 context capture Web UI（端口 9002），读取 second 环境的 gateway 日志 |

### 配置要点

- 状态目录：`%USERPROFILE%\.openclaw-second\`
- 配置文件：`%USERPROFILE%\.openclaw-second\openclaw.json`
- **没有安装 OpenViking 插件**，`contextEngine` 使用默认的 `legacy`
- **`memory-core` 照常启用**（无 `plugins` 配置节，`memory` 槽位走默认值 `memory-core`）
- 日志文件（如已初始化）：`%USERPROFILE%\.openclaw-second\logs\openclaw-profile-second.log`
- Capture 数据目录：`ai_toolbox/openclaw_capture_context_tool/data/context_capture_profile_second/`

---

## memory-core 说明

**默认环境已关闭 `memory-core`；second 环境保持默认启用。**

`memory-core`（`@openclaw/memory-core`）是 OpenClaw 自带的核心记忆搜索插件，占据 `memory` 槽位。它与 OpenViking 占据的 `contextEngine` 槽位互不干扰。

- 源码位置：`openclaw/extensions/memory-core/`
- 默认槽位定义：`openclaw/src/plugins/slots.ts` → `DEFAULT_SLOT_BY_KEY.memory = "memory-core"`
- 默认环境的 `openclaw.json` 中已将 `plugins.slots.memory` 设为 `"none"` 并设置 `plugins.entries.memory-core.enabled = false`，因此 **`memory-core` 在默认环境不会运行**
- second 环境未配置 `plugins` 节，`memory` 槽位走默认值，`memory-core` 正常启用
- 若要关闭 `memory-core`，在 `openclaw.json` 中设置 `plugins.slots.memory = "none"` 即可；也可进一步设置 `plugins.entries.memory-core.enabled = false` 确保彻底禁用

---

## Profile 机制原理

`--profile <name>` 由 `openclaw/src/cli/profile.ts` 处理，核心逻辑：

```
stateDir = ~/.openclaw-{name}     （name=default 时无后缀）
configPath = {stateDir}/openclaw.json
```

即 `--profile second` → `~/.openclaw-second/`，实现完整隔离：配置、会话、凭据、缓存各自独立。

> **注意**：仓库中还有一个 `openclaw/scripts/run-openclaw-instance2.ps1`，使用的目录是 `~/.openclawsecond`（无连字符），这是另一套更早期的实例方案，与 `--profile second`（`~/.openclaw-second`，有连字符）**不是**同一套。日常测试请使用 `--profile second`。

---

## 常用操作速查

```powershell
# 查看默认环境状态
openclaw status

# 查看 second 环境状态
openclaw --profile second status

# 默认环境跑测试
openclaw gateway

# second 环境跑测试
openclaw --profile second gateway --port 18890

# 停止 second 环境 gateway
openclaw --profile second gateway stop
```

---

## 两套环境的典型使用场景

| 场景 | 推荐环境 |
|------|----------|
| 测试 OpenViking 长期记忆效果 | 默认环境 |
| 对比有/无 OpenViking 的行为差异 | 两套同时运行，对比观察 |
| 调试 OpenClaw 原生行为（不受外部插件干扰） | second 环境 |
| context capture 分析 | second 环境（已配置 cache-trace） |
