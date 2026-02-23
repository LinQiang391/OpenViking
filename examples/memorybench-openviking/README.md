# OpenViking + MemoryBench (supermemoryai/memorybench)

这个示例把 OpenViking 作为 `memorybench` 的一个 Provider，方便直接跑 `longmemeval/locomo/convomem`。

## 目录说明

- `install_openviking_provider.py`：把 OpenViking provider 注入到本地 MemoryBench 仓库（幂等，可重复运行）
- `openviking_provider.ts`：Provider 实现模板（拷贝到 MemoryBench 的 `src/providers/openviking/index.ts`）
- `run_openviking_memorybench.sh`：一键安装 provider + 运行 benchmark

## 0. 前置条件

- 本机可运行 OpenViking HTTP Server（`openviking serve`）
- 本机已安装 `bun`（MemoryBench 用 bun 运行）
- 有可用的评测/回答模型 API Key（例如 `OPENAI_API_KEY`）

## 1. 准备 MemoryBench

```bash
git clone https://github.com/supermemoryai/memorybench.git /tmp/memorybench
cd /tmp/memorybench
bun install
```

## 2. 注入 OpenViking Provider

在 OpenViking 仓库里执行：

```bash
python3 examples/memorybench-openviking/install_openviking_provider.py \
  --memorybench-path /tmp/memorybench
```

会修改这些文件：

- `src/providers/openviking/index.ts`（新增）
- `src/types/provider.ts`
- `src/providers/index.ts`
- `src/utils/config.ts`

## 3. 启动 OpenViking Server

建议使用单独的数据目录，避免和你现有数据互相污染。

```bash
openviking serve --host 127.0.0.1 --port 1933
```

## 4. 配置环境变量

```bash
export OPENVIKING_BASE_URL="http://127.0.0.1:1933"
# 如果 server 配了 api_key，再设置这个
export OPENVIKING_API_KEY="your-key"

# MemoryBench 的 judge/answering model 依赖
export OPENAI_API_KEY="..."
```

## 5. 运行 benchmark

### 方式 A：直接用 MemoryBench 命令

```bash
cd /tmp/memorybench
bun run src/index.ts run \
  -p openviking \
  -b longmemeval \
  -j gpt-4o \
  -m gpt-4o-mini \
  -r ov-longmem-smoke \
  -l 5
```

### 方式 B：用示例脚本一键跑

```bash
bash examples/memorybench-openviking/run_openviking_memorybench.sh \
  /tmp/memorybench longmemeval 5 ov-longmem-smoke
```

结果在：

- `/tmp/memorybench/runs/ov-longmem-smoke/`

## 6. 说明（重要）

- OpenViking 当前是全局记忆空间，MemoryBench 的 `containerTag` 不是原生隔离维度。
- 这个 provider 在 ingest 时会做“记忆文件前后快照 diff”，把变化 URI 归因到当前问题，再在 search 阶段优先过滤这些 URI，尽量减少题间串扰。
- search 最终返回给 answering model 的主内容是每个 `uri` 对应的 `overview`（`/api/v1/content/overview`），并保留 `uri/score/abstract/raw` 作为辅助字段。
- 为了保证这个归因稳定，provider 内部把 ingest/indexing 并发设成了 1（会比云服务型 provider 更慢）。

## 7. 常见问题

- `Cannot find module '@ai-sdk/openai'`：通常是没执行 `bun install`，先在 MemoryBench 目录安装依赖。
- `bun run ...` 偶发崩溃：先重试一次；如果持续崩溃，升级 Bun 到最新版后重跑。
