# Benchmark 改动记录

## 1. 每轮注入后触发 OpenClaw compact

**文件**: `eval.py`

### 新增 `trigger_openclaw_compact` 函数

通过 WebSocket JSON-RPC 调用 OpenClaw Gateway 的 `sessions.compact` 方法，在每轮会话注入后触发上下文压缩。

- 协议版本: v3
- 客户端身份: `openclaw-control-ui` + `webchat` mode（绕过 CLI token 无 device identity 时 scopes 被清空的限制）
- 认证: token auth, 请求 `operator.admin` scope
- 依赖: `websocket-client` (`pip install websocket-client`)

### 修改 `run_ingest` 流程

`--memory-mode` 不为 `none` 时，每轮 `send_message` 成功后、`reset_session` 前调用 compact：

```
send_message()  → 注入对话（同时走 agent runner，memoryFlush / OV 插件 afterTurn 可在此触发）
    ↓
trigger_openclaw_compact()  → LLM 摘要压缩（+ OV 插件 compact handler）
    ↓
reset_session()  → 归档 session 文件
```

### 注意

- `sessions.compact` 只做上下文 LLM 压缩，**不触发 memcore memoryFlush**
- memoryFlush 在 `send_message` 的 agent runner 流程中触发（需配置启用）
- OpenViking 插件的 compact handler 会在 compact 时自动调用 `session.commit`

---

## 2. 启用 compaction.memoryFlush 配置

**文件**: `run_benchmark.py`, `config.toml`

### `config.toml` 新增 `[compaction]` 段

```toml
[compaction]
memory_flush_enabled = true
# reserve_tokens_floor = 20000
```

### `generate_openclaw_json` 支持写入 compaction 配置

仅在 `memory.mode` 为 `memcore` 或 `both` 时写入：

```json
{
  "agents": {
    "defaults": {
      "compaction": {
        "memoryFlush": { "enabled": true }
      }
    }
  }
}
```

---

## 3. 多记忆模式支持（memcore / openviking / both / none）

**文件**: `config.toml`, `eval.py`, `run_benchmark.py`

### 设计原则

端到端测试：ingest 和 QA 都走 OpenClaw `/v1/responses` API，**eval.py 核心逻辑与上游保持一致**，仅增加 compact 触发。记忆模式的差异完全通过 `openclaw.json` 配置（插件加载、compaction 设置）实现。

| 模式 | openclaw.json 差异 | compact 后效果 |
|---|---|---|
| `memcore` | `compaction.memoryFlush` 启用 | LLM 压缩 + 内置 memory_store 持久化 |
| `openviking` | 加载 OpenViking 插件 | afterTurn 抓取 → compact 触发 session.commit → 记忆提取 |
| `both` | 两者都配 | memcore 持久化 + OV 记忆提取 |
| `none` | 无增强 | 不触发 compact（基线对比） |

### `config.toml` 新增

```toml
[memory]
mode = "memcore"  # "memcore" | "openviking" | "both" | "none"

[openviking]
mode = "remote"                      # "remote" | "local"
base_url = "http://127.0.0.1:8080"
api_key = ""
auto_capture = true
auto_recall = true
commit_token_threshold = 0           # 0 = 每轮都 commit（测评推荐）
ingest_reply_assist = true
```

### `eval.py` 改动（最小化，核心逻辑与上游一致）

1. 新增 `trigger_openclaw_compact()` 函数（WebSocket RPC）
2. 新增 `--memory-mode` 参数（memcore/openviking/both/none），`--compact` 保留为兼容别名
3. `run_ingest` 中：`memory_mode != "none"` 时触发 `sessions.compact`
4. 完成后触发 warmup 请求构建记忆索引
5. **不直接调 OpenViking API**，由 OpenClaw 插件链路自动处理
6. `send_message`、`reset_session`、`process_single_question` 等核心函数签名与上游完全一致

### `run_benchmark.py` 改动

1. `generate_openclaw_json` 根据 `memory.mode` 生成不同配置：
   - memcore/both → 写入 `compaction.memoryFlush`
   - openviking/both → 在 `plugins.entries` 加入 `openviking` 插件配置（mode、baseUrl、autoCapture、autoRecall、commitTokenThreshold 等）
2. `step_ingest` 从 `[memory].mode` 读取模式，传递 `--memory-mode` 给 eval.py
3. main 启动时打印当前 memory mode

---

## 依赖变更

| 包 | 版本 | 用途 |
|---|---|---|
| `websocket-client` | 1.9.0 | WebSocket RPC 调用 OpenClaw compact |

安装: `pip install websocket-client`

---

## 技术发现

### OpenClaw compact 权限问题

- CLI 客户端 (`id: "cli"`, `mode: "cli"`) 使用 token 认证且无 device identity 时，Gateway 会清空自声明的 scopes → `sessions.compact` 报 `missing scope: operator.admin`
- 解决方案: 使用 `openclaw-control-ui` 客户端身份，配合 Gateway 的 `controlUi.allowInsecureAuth=true` 配置 + 本地连接，scopes 被保留

### memoryFlush 触发路径

- **触发位置**: `agent-runner.ts` → `runMemoryFlushIfNeeded()`（reply pipeline）
- **不触发位置**: `sessions.compact` WS RPC → `compactEmbeddedPiSession()`
- **依赖**: 需要 `memory-core` 插件注册 `flushPlanResolver`（内置默认加载）

### OpenViking context-engine 激活的关键配置 (2026-04-16)

- **问题**: OpenViking 插件注册了 context-engine（afterTurn, compact, assemble），但实测发现 afterTurn/compact 从未被调用
- **根因**: 缺少 `plugins.slots.contextEngine: "openviking"` 配置。OpenClaw 使用 `plugins.slots.contextEngine` 决定哪个 context-engine 处于激活状态，默认使用内置 legacy engine
- **解决**: 在 `openclaw.json` 中加入 `"plugins": { "slots": { "contextEngine": "openviking" } }`
- **验证结果**:
  - `assemble_entry` → context-engine 的 assemble 被调用 ✓
  - `afterTurn_entry` → 每轮对话自动捕获到 OV session ✓
  - `afterTurn_commit` → `commitTokenThreshold=0` 时每轮自动 commit ✓（status=accepted, archived=true）
  - `before_prompt_build` → auto-recall 正常工作 ✓
- **对 benchmark 的影响**: `run_benchmark.py` 的 `generate_openclaw_json` 在 openviking/both 模式下自动生成 `plugins.slots.contextEngine` 配置
- **备注**: 有了 afterTurn 自动 capture + commit，`trigger_openclaw_compact` 在 openviking 模式下可能不再必需，但保留以确保 memcore 模式兼容
