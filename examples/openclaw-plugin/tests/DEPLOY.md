# OpenClaw Gateway 部署脚本使用指南

## 前置要求

- **Python 3.8+**（脚本本身零外部依赖，只用标准库）
- **openclaw** 已通过 npm 全局安装（`openclaw --version` 可运行）
- **openviking** 已通过 pip 安装（如需 OpenViking 插件）
- **openviking server** 已启动（如需 OpenViking 插件的 remote 模式）
- **ov-install**（非 repo 场景需要，`npm i -g openclaw-openviking-setup-helper`）

## 脚本位置

```
examples/openclaw-plugin/tests/deploy_gateway.py
```

## 核心特性

- **部署即可用**：生成完整的 `openclaw.json`（含模型、认证、工作区、插件），创建所有必要目录
- **完全隔离**：每个 profile 拥有独立的状态目录、配置、会话、工作区、日志
- **灵活配置**：通过参数控制 OpenViking、memory-core 开关、模型提供商、API key 等

---

## 插件安装行为

脚本根据运行位置自动选择插件安装方式：

| 场景 | 判断条件 | 行为 |
|------|----------|------|
| **In-repo** | 脚本在 OpenViking 仓库中 | `plugins.load.paths` 直接指向源码 `examples/openclaw-plugin`，不复制文件 |
| **Non-repo** | 脚本不在 OpenViking 仓库中 | 通过 `ov-install --workdir <state_dir> -y` 下载插件到 `extensions/` |

**In-repo 模式特点**：
- 无需网络请求，秒级完成
- 改源码后重启 gateway 立即生效
- `load.paths` 指向 `examples/openclaw-plugin` 目录

**Non-repo 模式特点**：
- 自动检测是否已有插件，新装用 `ov-install -y`，已有用 `ov-install --update -y`
- 设置 `SKIP_OPENVIKING=1` 跳过 pip 安装
- 脚本的 config 最后写入，确保 openclaw.json 不被 ov-install 覆盖
- `load.paths` 指向 `<state_dir>/extensions/`

> **注意**：非 repo 模式下 `ov-install` 可能修改 `~/.openviking/ov.conf`，但脚本会自动备份并恢复（安装前后对比，如有变化则还原）。In-repo 模式不会触发 ov-install，不存在此问题。

---

## 环境隔离原理

每个 profile 对应一个独立的状态目录，**所有数据完全隔离**：

```
~/.openclaw/                  ← profile: default (端口 18789)
  ├── openclaw.json           ← 配置文件
  ├── agents/main/sessions/   ← 会话数据
  ├── workspace/              ← 工作区（git）
  ├── memory/                 ← memory-core 数据
  ├── logs/                   ← 日志
  ├── devices/                ← 设备信息
  ├── identity/               ← 身份信息
  └── canvas/                 ← Canvas UI

~/.openclaw-second/           ← profile: second (端口 18890)
  └── （同上，完全独立的一套）

~/.openclaw-<name>/           ← profile: <name> (自定义端口)
  └── （同上，完全独立的一套）
```

不同 profile 可以同时运行，使用不同端口，互不干扰。

---

## 一、环境部署方案（四种插件组合）

OpenViking 和 memory-core 组合成 **2×2 = 4 种场景**，对应方案 A~D：

| 方案 | OpenViking | memory-core | Profile | 端口 | 用途 |
|------|:---------:|:----------:|---------|:----:|------|
| **A** | ✅ 启用 | ❌ 关闭 | `default` | 18789 | 测试 OV 长期记忆（推荐） |
| **B** | ❌ 关闭 | ❌ 关闭 | `second` | 18890 | 无记忆基线，A/B 对比 |
| **C** | ✅ 启用 | ✅ 启用 | `both` | 19100 | OV + mem-core 协同效果 |
| **D** | ❌ 关闭 | ✅ 启用 | `memcore-only` | 19001 | 仅 mem-core，对比 OV |

> **关键参数说明**：
> - OpenViking 默认启用，加 `--no-openviking` 关闭
> - memory-core 默认关闭，加 `--mem-core` 启用
> - 同时关闭时自动禁用 `memory-core`、`memory-lancedb` 两个内存插件
> - 四种方案可同时运行（使用不同 profile 和端口）

---

### 方案 A：仅 OpenViking（推荐，测试长期记忆）

OV 插件接入 + memory-core 关闭。适合测试 OpenViking 长期记忆效果。

**直接执行**（PowerShell，在项目根目录）：

```powershell
# 1. 先在另一个终端启动 OpenViking server
openviking-server

# 2. 部署并启动 gateway（参数全部显式指定）
python examples/openclaw-plugin/tests/deploy_gateway.py --profile default --port 18789 --no-mem-core --ov-api-key "testapikey" --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 3. 验证
openclaw --profile default health
openclaw --profile default tui
```

**插件配置效果**：

```json
{
  "slots": { "memory": "none", "contextEngine": "openviking" },
  "entries": {
    "memory-core": { "enabled": false },
    "memory-lancedb": { "enabled": false },
    "openviking": {
      "enabled": true,
      "config": {
        "mode": "remote",
        "baseUrl": "http://127.0.0.1:1933",
        "apiKey": "testapikey",
        "agentId": "process",
        "logFindRequests": true,
        "autoCapture": true,
        "autoRecall": true,
        "emitStandardDiagnostics": true
      }
    }
  }
}
```

| 配置项 | 值 |
|--------|-----|
| Profile | `default` |
| 状态目录 | `~/.openclaw/` |
| 端口 | 18789 |
| 主模型 | `volcengine-plan/doubao-seed-2-0-code-preview-260215` |
| OpenViking | ✅ 启用（remote → `http://127.0.0.1:1933`） |
| memory-core | ❌ 关闭（`slots.memory = "none"`） |

**停止**：`openclaw gateway stop`，OpenViking 在其终端 Ctrl+C。

---

### 方案 B：无插件基线（A/B 对比）

不启用任何记忆插件，用作 A/B 对比的基线。

**直接执行**：

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py --profile second --port 18890 --no-openviking --no-mem-core --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 验证
openclaw --profile second health
openclaw --profile second tui
```

**插件配置效果**：

```json
{
  "enabled": false
}
```

| 配置项 | 值 |
|--------|-----|
| Profile | `second` |
| 状态目录 | `~/.openclaw-second/` |
| 端口 | 18890 |
| OpenViking | ❌ 关闭 |
| memory-core | ❌ 关闭 |

**停止**：`openclaw --profile second gateway stop`

---

### 方案 C：OpenViking + memory-core 双插件

同时启用 OpenViking 和 OpenClaw 自带记忆，测试两者协同效果。

**直接执行**：

```powershell
# 确保 OpenViking server 已运行
openviking-server

# 部署（注意加 --mem-core）
python examples/openclaw-plugin/tests/deploy_gateway.py --profile both --port 19100 --mem-core --ov-api-key "testapikey" --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 验证
openclaw --profile both health
openclaw --profile both tui
```

**插件配置效果**：

```json
{
  "slots": { "memory": "memory-core", "contextEngine": "openviking" },
  "entries": {
    "memory-core": { "enabled": true },
    "memory-lancedb": { "enabled": false },
    "openviking": {
      "enabled": true,
      "config": {
        "mode": "remote",
        "baseUrl": "http://127.0.0.1:1933",
        "apiKey": "testapikey",
        "agentId": "process",
        "logFindRequests": true,
        "autoCapture": true,
        "autoRecall": true,
        "emitStandardDiagnostics": true
      }
    }
  }
}
```

| 配置项 | 值 |
|--------|-----|
| Profile | `both` |
| 状态目录 | `~/.openclaw-both/` |
| 端口 | 19100 |
| OpenViking | ✅ 启用（`slots.contextEngine = "openviking"`） |
| memory-core | ✅ 启用（`slots.memory = "memory-core"`） |

**停止**：`openclaw --profile both gateway stop`

---

### 方案 D：仅 memory-core

只用 OpenClaw 自带记忆，不接入 OpenViking。适合对比 memory-core vs OpenViking 效果。

**直接执行**：

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py --profile memcore-only --port 19001 --no-openviking --mem-core --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 验证
openclaw --profile memcore-only health
openclaw --profile memcore-only tui
```

**插件配置效果**：

```json
{
  "slots": { "memory": "memory-core", "contextEngine": "legacy" },
  "entries": {
    "memory-core": { "enabled": true },
    "memory-lancedb": { "enabled": false }
  }
}
```

| 配置项 | 值 |
|--------|-----|
| Profile | `memcore-only` |
| 状态目录 | `~/.openclaw-memcore-only/` |
| 端口 | 19001 |
| OpenViking | ❌ 关闭 |
| memory-core | ✅ 启用（`slots.memory = "memory-core"`） |

**停止**：`openclaw --profile memcore-only gateway stop`

---

### 方案 E：裸 OpenClaw（无插件，自定义端口）

> 纯净 OpenClaw，不启用任何记忆/上下文插件。

```powershell
# ── 部署 + 启动 ──────────────────────────────────────────────
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile bare \
    --port 20080 \
    --no-openviking \
    --no-mem-core \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# ── 验证 ─────────────────────────────────────────────────────
curl http://127.0.0.1:20080/health
```

**停止**：

```powershell
openclaw --profile bare gateway stop
```

---

### 方案 F：自定义 profile + 从已有配置导入模型

> 基于现有环境的模型配置，快速创建新的隔离环境。

```powershell
# ── 从默认环境导入模型，创建新环境 ────────────────────────────
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile experiment \
    --port 19000 \
    --models-from ~/.openclaw/openclaw.json \
    --mem-core

# ── 验证 ─────────────────────────────────────────────────────
curl http://127.0.0.1:19000/health
```

**生成的环境**：

| 配置项 | 值 |
|--------|-----|
| Profile | `experiment` |
| 状态目录 | `~/.openclaw-experiment/` |
| 端口 | 19000 |
| 模型 | 从 `~/.openclaw/openclaw.json` 导入 |
| OpenViking | 启用（默认） |
| memory-core | 启用 |

**停止**：

```powershell
openclaw --profile experiment gateway stop
```

---

### 方案 G：远程 OpenViking server

> 连接部署在远程服务器上的 OpenViking。

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --ov-url http://10.0.0.1:1933 \
    --ov-api-key "your-remote-api-key" \
    --ov-agent-id "remote-agent" \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
```

---

### 方案 H：使用 ZAI（智谱）作为主模型

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile zai-test \
    --port 19002 \
    --primary-model "zai/glm-5" \
    --zai-key "your-zai-api-key" \
    --no-openviking
```

---

## 二、模型配置详解

### 方式一：使用内置模型预设（默认）

脚本内置了 volcengine 和 zai 两个模型提供商。传入 API key 即可使用。

```powershell
# 通过参数传入 API key
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8" \
    --zai-key "your-zai-key"

# 或通过环境变量
$env:VOLCENGINE_API_KEY = "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
$env:ZAI_API_KEY = "your-zai-key"
python examples/openclaw-plugin/tests/deploy_gateway.py
```

内置模型清单：

| 提供商 | 模型 ID | 说明 |
|--------|---------|------|
| volcengine-plan | `doubao-seed-2-0-code-preview-260215` | Doubao Seed 2.0 Code Preview |
| zai | `glm-5` | GLM-5 |
| zai | `glm-4.7` | GLM-4.7 |
| zai | `glm-4.7-flash` | GLM-4.7 Flash |

### 方式二：从现有配置导入

```powershell
# 从默认环境的 openclaw.json 导入全部模型/认证配置
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --models-from ~/.openclaw/openclaw.json \
    --profile new-env \
    --port 19000

# 从 second 环境导入
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --models-from ~/.openclaw-second/openclaw.json \
    --profile another-env \
    --port 19001
```

导入的内容包括：`models`（提供商、模型列表、API key）、`auth`（认证 profile）、`agents.defaults.model`（主模型）、`agents.defaults.models`（可用模型列表）。

### 方式三：合并到已有配置

已经手动配好模型的环境，用 `--merge` 只更新插件和 gateway 设置：

```powershell
# 保留 second 环境已有的模型配置，只更新插件设置
python examples/openclaw-plugin/tests/deploy_gateway.py --merge --profile second --mem-core

# 保留默认环境的模型，切换到 no-openviking
python examples/openclaw-plugin/tests/deploy_gateway.py --merge --no-openviking
```

### 指定主模型

```powershell
# 使用 GLM-5 作为主模型
python examples/openclaw-plugin/tests/deploy_gateway.py --primary-model "zai/glm-5"

# 使用 volcengine 模型
python examples/openclaw-plugin/tests/deploy_gateway.py --primary-model "volcengine-plan/doubao-seed-2-0-code-preview-260215"
```

---

## 三、同时运行多套环境

每套环境使用不同 profile 和端口，可以同时运行：

```powershell
# ── 终端 1：启动 OpenViking server ──────────────────────────
openviking-server

# ── 终端 2：环境 A — OpenViking + OpenClaw（端口 18789）─────
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# ── 终端 3：环境 B — 纯 OpenClaw（端口 18890）───────────────
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile second \
    --port 18890 \
    --no-openviking \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# ── 终端 4：环境 C — memory-core 环境（端口 19001）──────────
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile memcore \
    --port 19001 \
    --no-openviking \
    --mem-core \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
```

验证全部在线：

```powershell
curl http://127.0.0.1:1933/health     # OpenViking
curl http://127.0.0.1:18789/health    # 环境 A
curl http://127.0.0.1:18890/health    # 环境 B
curl http://127.0.0.1:19001/health    # 环境 C
```

全部停止：

```powershell
openclaw gateway stop
openclaw --profile second gateway stop
openclaw --profile memcore gateway stop
# OpenViking: 在其终端 Ctrl+C
```

---

## 四、抓包 / Cache-Trace

### 环境一 + 抓包

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --cache-trace \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
```

trace 数据默认写入 `./trace_data/`。

### 环境二 + 抓包（指定输出目录）

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile second \
    --port 18890 \
    --no-openviking \
    --cache-trace \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
# trace 数据自动写到 ~/.openclaw-second/logs/trace/
```

### 自定义环境 + 抓包

```powershell
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile trace-test \
    --port 20080 \
    --mem-core \
    --cache-trace \
    --verbose \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
# trace 数据自动写到 ~/.openclaw-trace-test/logs/trace/
```

输出文件：

| 文件 | 说明 |
|------|------|
| `cache-trace.jsonl` | 完整的 prompt / response 数据 |
| `openviking-diagnostics.jsonl` | OpenViking 插件诊断日志 |

---

## 五、配置管理命令

### 只生成配置，不启动

```powershell
# 生成默认环境配置
python examples/openclaw-plugin/tests/deploy_gateway.py --config-only \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 生成 second 环境配置
python examples/openclaw-plugin/tests/deploy_gateway.py --config-only \
    --profile second --port 18890 --no-openviking \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
```

生成后手动启动：

```powershell
openclaw gateway                                    # 默认环境
openclaw --profile second gateway --port 18890      # second 环境
```

### 预览配置（不写文件）

```powershell
# 预览默认环境配置
python examples/openclaw-plugin/tests/deploy_gateway.py --dry-run

# 预览 second 环境配置
python examples/openclaw-plugin/tests/deploy_gateway.py --dry-run --profile second --port 18890 --no-openviking

# 预览 OpenViking + mem-core 配置
python examples/openclaw-plugin/tests/deploy_gateway.py --dry-run --mem-core
```

### 合并到已有配置

```powershell
# 保留已有 models/auth，只更新插件
python examples/openclaw-plugin/tests/deploy_gateway.py --merge --profile second --mem-core

# 保留已有配置，加入 OpenViking
python examples/openclaw-plugin/tests/deploy_gateway.py --merge --profile second
```

### 重置配置

```powershell
# 删除旧配置，从零重建
python examples/openclaw-plugin/tests/deploy_gateway.py --reset \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 重置 second 环境
python examples/openclaw-plugin/tests/deploy_gateway.py --reset --profile second --port 18890 --no-openviking \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
```

---

## 六、LoCoMo 评估测试

### 方式一：通过 OpenViking（vikingbot）直连测试

```powershell
cd benchmark/locomo/vikingbot

# Step 1: 导入 LoCoMo 对话数据到 OpenViking
uv run python import_to_ov.py \
    --input ./test_data/locomo10.json \
    --openviking-url http://localhost:1933 \
    --parallel 5 \
    --force-ingest

# 等待索引完成（约 3 分钟）
Start-Sleep -Seconds 180

# Step 2: 运行 QA 评估
uv run python run_eval.py \
    ./test_data/locomo_qa_1528.csv \
    --output ./result/locomo_result.csv \
    --threads 20

# Step 3: 裁判打分
uv run python judge.py \
    --input ./result/locomo_result.csv \
    --parallel 10

# Step 4: 统计结果
uv run python stat_judge_result.py \
    --input ./result/locomo_result.csv
```

### 方式二：通过 OpenClaw gateway 测试（环境一，有 OpenViking）

```powershell
cd benchmark/locomo/openclaw

# Ingest 对话数据
uv run python eval.py ingest \
    ./test_data/locomo10.json \
    --base-url http://127.0.0.1:18789 \
    --token "6bde452fb5bab8ac3ca643817e53f80334a224bfd5fb6d19" \
    --sample 0 \
    --sessions 1-4

# 运行 QA 评估
uv run python eval.py qa \
    ./test_data/locomo10.json \
    --base-url http://127.0.0.1:18789 \
    --token "6bde452fb5bab8ac3ca643817e53f80334a224bfd5fb6d19" \
    --sample 0 \
    --output ./result/qa_env1
```

### 方式三：通过 OpenClaw gateway 测试（环境二，无 OpenViking）

```powershell
cd benchmark/locomo/openclaw

# Ingest
uv run python eval.py ingest \
    ./test_data/locomo10.json \
    --base-url http://127.0.0.1:18890 \
    --token "<second环境的token>" \
    --sample 0 \
    --sessions 1-4

# QA
uv run python eval.py qa \
    ./test_data/locomo10.json \
    --base-url http://127.0.0.1:18890 \
    --token "<second环境的token>" \
    --sample 0 \
    --output ./result/qa_env2
```

> **提示**：每次部署时脚本会输出 auth token，也可以在 `~/.openclaw-<profile>/openclaw.json` 中找到 `gateway.auth.token` 字段。

---

## 七、连接已部署的 Gateway

部署完成后有多种方式连接到 gateway。

### 方式一：TUI（终端交互界面，推荐）

直接在终端打开 OpenClaw 的交互式 TUI，连接到对应 profile 的 gateway 聊天。

```powershell
# 连接默认环境
openclaw tui

# 连接 second 环境
openclaw --profile second tui

# 连接 third 环境
openclaw --profile third tui

# 连接任意自定义 profile
openclaw --profile <name> tui
```

> TUI 启动后会自动连接到该 profile 对应的运行中的 gateway，进入后直接输入消息即可聊天。

### 方式二：Dashboard（浏览器 Web UI）

```powershell
# 打开默认环境的 Dashboard
openclaw dashboard

# 打开 second 环境的 Dashboard
openclaw --profile second dashboard

# 打开 third 环境的 Dashboard
openclaw --profile third dashboard
```

### 方式三：Canvas（浏览器直接访问）

直接在浏览器打开：

```
http://127.0.0.1:18789/__openclaw__/canvas/    # 默认环境
http://127.0.0.1:18890/__openclaw__/canvas/    # second 环境
http://127.0.0.1:20080/__openclaw__/canvas/    # third 环境（端口按实际部署）
```

### 方式四：查看状态

```powershell
openclaw --profile third status    # 查看 third 环境状态
openclaw --profile third health    # 查看 gateway 健康状态
openclaw --profile third sessions list  # 查看会话列表
```

### 方式五：HTTP API

部署完成后，也可以用 curl 或任何 HTTP 客户端发送请求。

### OpenAI Responses API 格式

```powershell
# 环境一（默认）
curl -X POST http://127.0.0.1:18789/v1/responses `
    -H "Authorization: Bearer <token>" `
    -H "Content-Type: application/json" `
    -d '{"model":"openclaw","input":"你好，你记得我吗？","stream":false}'

# 环境二（second）
curl -X POST http://127.0.0.1:18890/v1/responses `
    -H "Authorization: Bearer <token>" `
    -H "Content-Type: application/json" `
    -d '{"model":"openclaw","input":"你好","stream":false}'

# 自定义环境（端口 20080）
curl -X POST http://127.0.0.1:20080/v1/responses `
    -H "Authorization: Bearer <token>" `
    -H "Content-Type: application/json" `
    -d '{"model":"openclaw","input":"hello","stream":false}'
```

### OpenAI Chat Completions API 格式

```powershell
curl -X POST http://127.0.0.1:18789/v1/chat/completions `
    -H "Authorization: Bearer <token>" `
    -H "Content-Type: application/json" `
    -d '{"model":"openclaw","messages":[{"role":"user","content":"hello"}],"stream":false}'
```

---

## 八、清理数据

### 清理单个环境的会话

```powershell
# 清理默认环境会话
Remove-Item -Recurse -Force "$env:USERPROFILE\.openclaw\agents\*\sessions\*"

# 清理 second 环境会话 + 记忆
Remove-Item -Recurse -Force "$env:USERPROFILE\.openclaw-second\agents\*\sessions\*"
Remove-Item -Force "$env:USERPROFILE\.openclaw-second\memory\main.sqlite" -ErrorAction SilentlyContinue

# 清理自定义环境
Remove-Item -Recurse -Force "$env:USERPROFILE\.openclaw-<name>\agents\*\sessions\*"
```

### 清理 OpenViking 数据

```powershell
# 清理 OpenViking 所有记忆和会话（最彻底）
Remove-Item -Recurse -Force "$env:USERPROFILE\.openviking\data\viking"
Remove-Item -Recurse -Force "$env:USERPROFILE\.openviking\data\vectordb"
Remove-Item -Recurse -Force "$env:USERPROFILE\.openviking\data\_system\queue"
```

### 删除整个环境

```powershell
# 删除自定义环境的全部数据
Remove-Item -Recurse -Force "$env:USERPROFILE\.openclaw-<name>"
```

> **注意**：清理前先停掉对应的 gateway 和 OpenViking server，避免文件锁定。

---

## 九、完整参数列表

### Profile & Gateway

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--profile NAME` | `default` | Profile 名称，用于环境隔离 |
| `--port PORT` | 自动 | 端口（default=18789, second=18890, 其他=19000） |
| `--auth-token TOKEN` | 自动生成 | Gateway 认证 token |

### 模型配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--models-from PATH` | — | 从已有 openclaw.json 导入模型/认证 |
| `--primary-model ID` | `volcengine-plan/doubao-seed-2-0-code-preview-260215` | 主模型 ID |
| `--volcengine-key KEY` | env `VOLCENGINE_API_KEY` | Volcengine API key |
| `--zai-key KEY` | env `ZAI_API_KEY` | ZAI（智谱）API key |

### 插件

| 参数 | 默认 | 说明 |
|------|------|------|
| `--openviking` | 启用 | 启用 OpenViking 插件 |
| `--no-openviking` | — | 关闭 OpenViking 插件 |
| `--mem-core` | 关闭 | 启用 memory-core 插件 |
| `--no-mem-core` | — | 关闭 memory-core 插件 |

### OpenViking 连接

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ov-mode MODE` | `remote` | 连接模式（`remote` / `local`） |
| `--ov-url URL` | `http://127.0.0.1:1933` | OpenViking 服务地址 |
| `--ov-api-key KEY` | **必填** | OpenViking API key（启用 OV 时必须提供） |
| `--ov-agent-id ID` | profile 名 | OpenViking agent ID 前缀（自动按 profile 隔离记忆） |
| `--plugin-dir PATH` | 自动检测 | openclaw-plugin 源码目录 |

### 行为控制

| 参数 | 说明 |
|------|------|
| `--cache-trace` | 开启 cache-trace 抓包 |
| `--trace-dir DIR` | 抓包数据输出目录（默认 `~/.openclaw[-<profile>]/logs/trace`） |
| `--verbose` | 详细日志模式 |
| `--config-only` | 只生成配置文件，不启动 gateway |
| `--dry-run` | 打印配置到终端，不写文件 |
| `--merge` | 合并到已有配置（保留 models/auth） |
| `--reset` | 删除旧配置后重新生成 |

---

## 十、典型工作流

### 工作流 1：从零开始测试 OpenViking

```powershell
# 1. 启动 OpenViking
openviking-server

# 2. 部署 gateway
$env:VOLCENGINE_API_KEY = "81491c7c-268e-4bf8-83b8-31ffba5b12c8"
python examples/openclaw-plugin/tests/deploy_gateway.py --ov-api-key "testapikey"

# 3. 脚本输出 auth token，记下来

# 4. 发送测试请求
curl -X POST http://127.0.0.1:18789/v1/responses `
    -H "Authorization: Bearer <输出的token>" `
    -H "Content-Type: application/json" `
    -d '{"model":"openclaw","input":"你好","stream":false}'
```

### 工作流 2：A/B 对比测试（有/无 OpenViking）

```powershell
# 启动 OpenViking
openviking-server

# 终端 A：有 OpenViking + 抓包
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --ov-api-key "testapikey" \
    --cache-trace \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 终端 B：无 OpenViking + 抓包
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --profile second --port 18890 --no-openviking \
    --cache-trace \
    --volcengine-key "81491c7c-268e-4bf8-83b8-31ffba5b12c8"

# 对比两个环境的 trace 数据
```

### 工作流 3：快速复制环境

```powershell
# 已有默认环境配置好了模型，快速克隆到新环境测试
python examples/openclaw-plugin/tests/deploy_gateway.py \
    --models-from ~/.openclaw/openclaw.json \
    --profile clone-test \
    --port 19999 \
    --no-openviking \
    --mem-core
```

### 工作流 4：LoCoMo 完整评估流水线

```powershell
# 1. 确保 OpenViking server 在运行
openviking-server

# 2. 导入测试数据
cd benchmark/locomo/vikingbot
uv run python import_to_ov.py --input ./test_data/locomo10.json --force-ingest

# 3. 等待索引
Start-Sleep -Seconds 180

# 4. QA 评估
uv run python run_eval.py ./test_data/locomo_qa_1528.csv --output ./result/locomo_result.csv --threads 20

# 5. 等待
Start-Sleep -Seconds 180

# 6. 裁判打分
uv run python judge.py --input ./result/locomo_result.csv --parallel 10

# 7. 统计
uv run python stat_judge_result.py --input ./result/locomo_result.csv
```

---

## 一键测试脚本 `run_test_a.py`

自动化 A 组（OpenViking 插件）的完整评测流程，一条命令完成清理→部署→注入→QA→出报告。

### 脚本位置

```
examples/openclaw-plugin/tests/
├── run_test_a.py       ← 一键测试脚本
├── config-A.json       ← A 组配置（API key、端口、测试参数）
├── deploy_gateway.py   ← 被调用：生成 openclaw.json + 安装插件
├── cleanup_gateway.py  ← 被调用：清理 gateway profile
└── cleanup_ov_data.py  ← 被调用：清理 OpenViking 数据
```

### 配置文件 `config-A.json`

```json
{
  "profile": "eval-ov",
  "gateway_port": 16000,
  "capture_port": 9010,
  "volcengine_key": "<模型推理 API key>",
  "openviking": {
    "url": "http://127.0.0.1:1933",
    "root_api_key": "testapikey",
    "ov_conf": null,
    "agent_id": "eval-ov",
    "account_id": "locomo-eval",
    "user_id": "eval-1"
  },
  "test": {
    "data_file": "locomo10.json",
    "sample": 0,
    "output_dir": "output/group-a",
    "ingest_output": "ingest_0.txt",
    "qa_output": "qa_answers.txt",
    "ingest_tail": "[remember what's said, keep existing memory]"
  },
  "cache_trace": true,
  "verbose": true,
  "cleanup_before_run": true
}
```

| 字段 | 说明 |
|------|------|
| `profile` | OpenClaw profile 名，决定状态目录 `~/.openclaw-<profile>/` |
| `gateway_port` | Gateway 监听端口 |
| `capture_port` | 抓包 Web UI 端口（设为 `null` 则不启动） |
| `volcengine_key` | Volcengine **模型推理** API key |
| `openviking.url` | OpenViking 服务器地址 |
| `openviking.root_api_key` | OpenViking 管理 API key（用于创建租户） |
| `openviking.ov_conf` | ov.conf 路径（`null` 使用默认 `~/.openviking/ov.conf`） |
| `test.data_file` | 测试数据文件名（相对于 `openclaw-eval/`） |
| `test.sample` | LoCoMo sample 索引 |
| `test.output_dir` | 输出目录（相对于 `openclaw-eval/`） |
| `cleanup_before_run` | 每次运行前是否自动清理环境 |

### 使用

```powershell
cd examples/openclaw-plugin/tests

# 完整流程（清理→部署→注入→QA→报告）
python run_test_a.py

# 指定配置文件
python run_test_a.py config-A.json

# 跳过清理（追加测试）
python run_test_a.py --skip-cleanup

# 只跑注入，不跑 QA
python run_test_a.py --skip-qa

# 只跑 QA（复用已有注入数据）
python run_test_a.py --skip-ingest
```

### 执行步骤

| Step | 操作 | 可跳过 |
|:----:|------|:------:|
| 0 | 清理环境（gateway profile + OV data + 输出） | `--skip-cleanup` |
| 1 | 启动 OpenViking 服务器（已运行则跳过） | — |
| 2 | 创建 OV 租户，动态获取 `user_key` | — |
| 3 | 调用 `deploy_gateway.py --config-only` 生成配置，动态获取 token | — |
| 4 | 后台启动 gateway（含 cache-trace 环境变量） | — |
| 5 | 启动抓包 Web UI（如配置了 `capture_port`） | — |
| 6 | 验证 API 连通性 | — |
| 7 | 执行 Ingest | `--skip-ingest` |
| 8 | 执行 QA | `--skip-qa` |
| 9 | 输出汇总，保持 gateway 运行 | — |

测试完成后 gateway 和抓包工具保持运行，按 `Ctrl+C` 停止所有子进程。

### 输出文件

测试结果写入 `openclaw-eval/<output_dir>/`：

```
openclaw-eval/output/group-a/
├── ingest_0.txt              ← ingest 摘要
├── ingest_0.txt.csv          ← ingest 逐条记录
├── qa_answers.txt            ← QA 摘要
├── qa_answers.txt.1.jsonl    ← QA 详细回答
├── ov_server.log             ← OpenViking 服务器日志
├── gateway.log               ← Gateway 日志
└── capture.log               ← 抓包工具日志
```
