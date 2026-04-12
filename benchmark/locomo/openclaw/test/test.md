# OpenClaw MemCore 记忆功能测试

测试 OpenClaw 内置 memory-core 插件的长期记忆能力，使用 LoCoMo benchmark 评估。

## 测试目标

验证 OpenClaw 自身的记忆系统（`MEMORY.md` + `memory/YYYY-MM-DD.md` + `memory_search`）在多轮对话后能否正确召回历史信息。

## 前置条件

- OpenClaw 已安装（`npm i -g openclaw`，版本 >= 2026.3.7）
- LLM 模型已配置（`~/.openclaw/openclaw.json`）
- 记忆搜索已配置 embedding provider（`memorySearch.provider`）
- LoCoMo 数据文件：`benchmark/locomo/data/locomo10.json`（已包含在仓库中，来源 [ZaynJarvis/openclaw-eval](https://github.com/ZaynJarvis/openclaw-eval/blob/main/locomo10.json)）
- Python 依赖：`pip install requests openai python-dotenv`

## Step 0: 清理环境

使用清理脚本清除旧的会话和记忆数据，确保干净的测试环境。**清理前会自动归档现有数据到 `archive/` 目录，防止数据丢失。**

```bash
cd benchmark/locomo/openclaw

# 预览将要删除的文件（不实际删除）
python test/clean_openclaw.py --dry-run

# 清除全部（先归档再清理），默认目录 ~/.openclaw
python test/clean_openclaw.py -y

# 指定 OpenClaw 目录和 agent ID
python test/clean_openclaw.py --openclaw-dir ~/.openclaw --agent-id locomo-eval -y

# 只清会话记录
python test/clean_openclaw.py --openclaw-dir ~/.openclaw --sessions-only -y

# 只清记忆文件和索引
python test/clean_openclaw.py --openclaw-dir ~/.openclaw --memory-only -y

# 跳过归档直接清理（危险！数据将永久丢失）
python test/clean_openclaw.py --no-archive -y

# 指定归档目录（默认 ./archive）
python test/clean_openclaw.py --archive-dir /path/to/archive -y
```

清理内容说明：

| 选项 | 清理范围 |
|------|---------|
| 默认（无 flag） | 先归档 → sessions JSONL + sessions.json + MEMORY.md + memory/*.md + memory index SQLite |
| `--sessions-only` | 先归档 → 仅 sessions JSONL + sessions.json |
| `--memory-only` | 先归档 → 仅 MEMORY.md + memory/*.md + memory index SQLite |
| `--no-archive` | 跳过归档，直接删除（危险） |

归档目录结构：

```
archive/{时间戳}_pre-clean/
├── sessions/          ← 会话文件备份
├── memory/            ← 记忆文件 + SQLite 索引备份
├── openclaw/          ← openclaw.json + agent 配置备份
└── archive_meta.json  ← 归档元数据
```

## Step 1: 配置 OpenClaw

本测试分两组对照实验。将对应的 JSON 复制到 `~/.openclaw/openclaw.json`，替换 `YOUR_API_KEY` 为你的火山引擎 API Key 即可。

### 实验组 A：memcore + embedding（语义搜索）

```json
{
  "agents": {
    "defaults": {
      "models": {
        "volcengine-plan/doubao-seed-2-0-code-preview-260215": {}
      },
      "model": {
        "primary": "volcengine-plan/doubao-seed-2-0-code-preview-260215"
      },
      "thinkingDefault": "adaptive",
      "memorySearch": {
        "provider": "openai",
        "model": "doubao-embedding-vision-250615",
        "remote": {
          "baseUrl": "https://ark.cn-beijing.volces.com/api/coding/v3/",
          "apiKey": "YOUR_API_KEY"
        },
        "query": {
          "hybrid": {
            "enabled": true,
            "vectorWeight": 0.7,
            "textWeight": 0.3
          },
          "minScore": 0
        }
      }
    }
  },
  "gateway": {
    "mode": "local",
    "auth": {
      "mode": "token",
      "token": "ff7a76dfa6e42792609c3c9159f5345ce1801949be94b9ab"
    },
    "port": 18789,
    "bind": "loopback",
    "tailscale": { "mode": "off" },
    "controlUi": { "allowInsecureAuth": true },
    "http": {
      "endpoints": {
        "responses": { "enabled": true }
      }
    }
  },
  "models": {
    "providers": {
      "volcengine-plan": {
        "baseUrl": "https://ark.cn-beijing.volces.com/api/coding",
        "apiKey": "YOUR_API_KEY",
        "api": "anthropic-messages",
        "models": [
          {
            "id": "doubao-seed-2-0-code-preview-260215",
            "name": "Doubao Seed 2.0 Code Preview",
            "reasoning": true,
            "input": ["text"],
            "contextWindow": 256000,
            "maxTokens": 4096
          }
        ]
      }
    }
  },
  "session": { "dmScope": "per-channel-peer" },
  "tools": { "profile": "coding" },
  "auth": {
    "profiles": {
      "volcengine:default": {
        "provider": "volcengine",
        "mode": "api_key"
      }
    },
    "order": {
      "volcengine": ["volcengine:default"]
    }
  },
  "plugins": {
    "entries": {
      "volcengine": { "enabled": true }
    }
  }
}
```

同时更新 `~/.openclaw/agents/main/agent/auth-profiles.json`：

```json
{
  "version": 1,
  "profiles": {
    "volcengine:default": {
      "type": "api_key",
      "provider": "volcengine",
      "key": "YOUR_API_KEY"
    }
  }
}
```

### 实验组 B：memcore 无 embedding（仅文件读取，无语义搜索）

与实验组 A 相同，但删除整个 `memorySearch` 配置块：

```json
{
  "agents": {
    "defaults": {
      "models": {
        "volcengine-plan/doubao-seed-2-0-code-preview-260215": {}
      },
      "model": {
        "primary": "volcengine-plan/doubao-seed-2-0-code-preview-260215"
      },
      "thinkingDefault": "adaptive"
    }
  },
  "gateway": {
    "mode": "local",
    "auth": {
      "mode": "token",
      "token": "ff7a76dfa6e42792609c3c9159f5345ce1801949be94b9ab"
    },
    "port": 18789,
    "bind": "loopback",
    "tailscale": { "mode": "off" },
    "controlUi": { "allowInsecureAuth": true },
    "http": {
      "endpoints": {
        "responses": { "enabled": true }
      }
    }
  },
  "models": {
    "providers": {
      "volcengine-plan": {
        "baseUrl": "https://ark.cn-beijing.volces.com/api/coding",
        "apiKey": "YOUR_API_KEY",
        "api": "anthropic-messages",
        "models": [
          {
            "id": "doubao-seed-2-0-code-preview-260215",
            "name": "Doubao Seed 2.0 Code Preview",
            "reasoning": true,
            "input": ["text"],
            "contextWindow": 256000,
            "maxTokens": 4096
          }
        ]
      }
    }
  },
  "session": { "dmScope": "per-channel-peer" },
  "tools": { "profile": "coding" },
  "auth": {
    "profiles": {
      "volcengine:default": {
        "provider": "volcengine",
        "mode": "api_key"
      }
    },
    "order": {
      "volcengine": ["volcengine:default"]
    }
  },
  "plugins": {
    "entries": {
      "volcengine": { "enabled": true }
    }
  }
}
```

> **注意**：切换实验组时，需要先运行 `python test/clean_openclaw.py -y` 清除上一组的会话和记忆数据，再重启 `openclaw gateway`。

## Step 2: 验证记忆功能

```bash
# 检查 memory search 状态
openclaw memory status

# 确认输出中显示：
# Provider: openai (requested: openai)
# Vector: ready
# FTS: ready
```

## Step 3: 启动 OpenClaw Gateway

```bash
openclaw gateway
```

记下 gateway token 或在配置中设定。

## Step 4: 导入对话数据（Ingest）

将 LoCoMo 对话通过 OpenClaw 的 `/v1/responses` 接口发送，让 agent 自动将内容写入记忆文件。

**重要**：由于单轮对话可能不够长，不足以自动触发 memoryFlush，建议加 `--compact` 参数在每轮注入后主动触发 `/compact`，强制将对话内容写入记忆。

```bash
# 导入所有 samples（推荐加 --compact）
python eval.py ingest ../data/locomo10.json \
    --token "YOUR_GATEWAY_TOKEN" \
    --compact

# 只导入第 0 个 sample（调试用）
python eval.py ingest ../data/locomo10.json \
    --token "YOUR_GATEWAY_TOKEN" \
    --sample 0 \
    --compact

# 强制重新导入
python eval.py ingest ../data/locomo10.json \
    --token "YOUR_GATEWAY_TOKEN" \
    --force-ingest \
    --compact

# 不触发 compact（原始行为，依赖 OpenClaw 自动 memoryFlush）
python eval.py ingest ../data/locomo10.json \
    --token "YOUR_GATEWAY_TOKEN"
```

导入后检查记忆文件是否生成：

```bash
# Windows
dir %USERPROFILE%\.openclaw\workspace\memory\
type %USERPROFILE%\.openclaw\workspace\MEMORY.md

# Linux/macOS
ls ~/.openclaw/workspace/memory/
cat ~/.openclaw/workspace/MEMORY.md
```

## Step 5: 等待索引构建

导入完成后等待 1-2 分钟，让 embedding 索引构建完成。

```bash
openclaw memory status
# 确认 Indexed 不为 0/0
```

## Step 6: 运行 QA 评估

```bash
# 运行 QA 评估（所有 samples）
python eval.py qa ../data/locomo10.json \
    --token "YOUR_GATEWAY_TOKEN" \
    --parallel 10

# 单个 sample + 限制问题数
python eval.py qa ../data/locomo10.json \
    --token "YOUR_GATEWAY_TOKEN" \
    --sample 0 \
    --count 5 \
    --parallel 5
```

结果自动保存到 `result/qa_results.csv`。

## Step 7: LLM 裁判打分

```bash
# 设置裁判模型 API key
# echo "ARK_API_KEY=your-key" > ~/.openviking_benchmark_env
# 或 export ARK_API_KEY=your-key

python judge.py --input result/qa_results.csv --parallel 40
```

## Step 8: 统计结果

```bash
python stat_judge_result.py --input result/qa_results.csv
```

## 一键运行（推荐）

使用 `run_benchmark.py` + `config.toml` 统一配置，一键完成全流程：

### 1. 配置

```bash
cd benchmark/locomo/openclaw

# 复制配置模板
cp config.toml config.local.toml
```

编辑 `config.local.toml`，填入你的 API Key 和 Gateway Token：

```toml
[vlm]
api_key = "YOUR_VLM_API_KEY"

[embedding]
enabled = true                           # false = 不用 embedding
api_key = "YOUR_EMBEDDING_API_KEY"

[gateway]
token = "YOUR_GATEWAY_TOKEN"

[judge]
api_key = "YOUR_JUDGE_API_KEY"

[general]
name = "memcore-embedding-v1"            # 归档目录名

[ingest]
sample = 0                               # -1 = 全部，0 = 只测 sample 0

[qa]
parallel = 5
sample = 0                               # -1 = 全部
```

完整配置项见 `config.toml` 文件注释。

### 2. 运行

```bash
# 一键全流程（clean → ingest → snapshot → QA → judge → stat → archive）
python run_benchmark.py --config config.local.toml

# 预览步骤（不实际执行）
python run_benchmark.py --config config.local.toml --dry-run

# 从上次中断处恢复（跳过 clean，利用已有数据继续）
python run_benchmark.py --config config.local.toml --resume

# 只运行指定步骤
python run_benchmark.py --config config.local.toml --only ingest,snapshot_ingest,qa

# 跳过指定步骤
python run_benchmark.py --config config.local.toml --skip judge,archive

# 只生成 openclaw.json（不运行测试）
python run_benchmark.py --config config.local.toml --generate-config-only
```

### 3. 流程说明

| 步骤 | 对应脚本 | 说明 |
|------|---------|------|
| `clean` | `test/clean_openclaw.py` | **先归档**现有数据 → 清理会话 + 记忆 + 索引 |
| (自动) | `run_benchmark.py` | 根据 config 生成 `openclaw.json` |
| `ingest` | `eval.py ingest` | 注入 LoCoMo 对话到 OpenClaw |
| `snapshot_ingest` | `run_benchmark.py` | 快照 ingest session 文件，与 QA 分离 |
| `qa` | `eval.py qa` | QA 评估，结果保存到 `result/qa_results.csv` |
| `judge` | `judge.py` | LLM 裁判打分 |
| `stat` | `stat_judge_result.py` | 统计准确率和 token 消耗 |
| `archive` | `test/archive_run.py` | 归档所有数据（ingest/qa session 分开）到 `archive/` |

### 断点续传

如果因为 API 限流等原因导致中途失败，可以用 `--resume` 恢复：

```bash
python run_benchmark.py --config config.local.toml --resume
```

`--resume` 会跳过 `clean` 和 `snapshot_ingest` 步骤，保留已有数据：
- **Ingest** 会跳过已注入的 session（靠 `.ingest_record.json` 记录）
- **QA** 会跳过已回答的问题（靠 CSV 记录）

### Session 文件命名

归档的 session 文件包含 phase 标签，便于区分来源：

| 阶段 | 文件名格式 |
|------|-----------|
| Ingest | `{uuid}.jsonl.ingest.{timestamp}` |
| QA | `{uuid}.jsonl.qa.{timestamp}` |

最终归档目录结构：

```
archive/{时间戳}_{名称}/
├── sessions/
│   ├── ingest/      ← ingest 阶段的 session 文件
│   └── qa/          ← QA 阶段的 session 文件
├── memory/          ← 记忆文件 + 索引
├── openclaw/        ← OpenClaw 配置快照
├── result/          ← QA 结果 CSV
└── run_meta.json    ← 运行元数据
```

### 4. 两组对比实验

```bash
# 实验组 A：memcore + embedding
# config.local.toml 中设置 [embedding] enabled = true
python run_benchmark.py --config config.local.toml

# 实验组 B：memcore 无 embedding
# config.local.toml 中设置 [embedding] enabled = false, [general] name = "memcore-no-embedding"
python run_benchmark.py --config config.local.toml
```

## 手动分步运行

如果不使用一键脚本，也可以手动执行每个步骤（见上方 Step 0 ~ Step 8）。

## 评估指标

| 指标 | 说明 |
|------|------|
| Accuracy | 正确回答数 / 总问题数（排除 category=5） |
| Token Usage | 每个问题的 input/output/cache token 消耗 |
| Category 1 | 事实记忆（直接信息召回） |
| Category 2 | 多跳推理（需关联多条记忆） |
| Category 3 | 时间推理（涉及时间线判断） |
| Category 4 | 开放问答 |

## 对比基线

| 配置 | 说明 |
|------|------|
| OpenClaw memcore only | 本测试：仅使用 OpenClaw 内置记忆 |
| OpenClaw + OpenViking | 使用 OpenViking 插件作为 context-engine |
| 无记忆 | 纯 LLM 回答，不启用任何记忆功能 |

## 工具脚本

| 脚本 | 用途 |
|------|------|
| `run_benchmark.py` | **统一入口**：一键运行全流程，读取 config.toml 配置 |
| `config.toml` | 配置模板（复制为 config.local.toml 使用） |
| `test/clean_openclaw.py` | 清除 OpenClaw 会话记录和记忆文件 |
| `test/archive_run.py` | 归档测试数据到 archive/ 目录 |
| `eval.py` | 数据导入（ingest）和 QA 评估 |
| `judge.py` | LLM 自动打分 |
| `stat_judge_result.py` | 结果统计 |
| `import_to_ov.py` | 导入到 OpenViking（对比测试用） |

## 常见问题

| 问题 | 解决方案 |
|------|---------|
| `Model context window too small` | 确保模型 contextWindow >= 16000 |
| 记忆文件为空 | 检查 agent 是否有写入权限，确认 memoryFlush 已启用 |
| `memory_search` 返回空（索引正常） | **关键**：`doubao-embedding-vision-250615` 的 hybrid 分数偏低（0.16~0.24），需在 `memorySearch.query` 中加 `"minScore": 0`，否则被默认阈值过滤。可用 `openclaw memory search --agent <id> --min-score 0 "query"` 验证 |
| `memory_search` 返回空（索引为空） | 检查 embedding provider 配置和 API key，确认索引已构建（`openclaw memory status --agent <id>`） |
| 准确率很低 | 确认记忆文件确实写入了关键信息，增加等待时间 |
| `Config was last written by a newer OpenClaw` | 版本不匹配警告，可忽略或升级 OpenClaw |
