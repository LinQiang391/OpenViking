# OpenViking Eval 模块

OpenViking 的评估模块，提供 RAG 系统的多维度评估能力。

## 模块作用

Eval 模块支持对 RAG 系统进行全面评估：

- **检索质量评估**：精确度、召回率、相关性
- **生成质量评估**：忠实度、答案相关性
- **性能评估**：检索速度、端到端延迟
- **框架集成**：支持 RAGAS、TruLens 等主流评测工具
- **存储层评估**：IO 操作录制与回放，对比不同存储后端性能

## 模块设计

```
openviking/eval/
├── types.py         # 数据类型：EvalSample, EvalDataset, EvalResult
├── base.py          # 评估器基类：BaseEvaluator
├── ragas.py         # RAGAS 框架集成
├── generator.py     # 数据集生成器
├── pipeline.py      # RAG 查询流水线
├── rag_eval.py      # CLI 评估工具
└── datasets/        # 示例数据集

openviking/storage/recorder/
├── __init__.py      # IORecorder 录制器
├── playback.py      # Playback 回放器
└── wrapper.py       # 存储层包装器
```

### 核心类型

```python
# 评估样本
EvalSample(
    query="问题",
    context=["检索上下文"],
    response="生成答案",
    ground_truth="标准答案"
)

# 评估数据集
EvalDataset(name="dataset", samples=[...])

# 评估结果
EvalResult(sample=..., scores={"faithfulness": 0.85})
```

### 评估器接口

```python
class BaseEvaluator(ABC):
    async def evaluate_sample(self, sample: EvalSample) -> EvalResult
    async def evaluate_dataset(self, dataset: EvalDataset) -> SummaryResult
```

## 安装方法

```bash
# 基础安装
pip install openviking

# RAGAS 评估支持
pip install ragas datasets
```

## 用法示例

### 示例 1：RAGAS 评估

```python
import asyncio
from openviking.eval import EvalSample, EvalDataset, RagasEvaluator

async def main():
    # 准备评估数据
    samples = [
        EvalSample(
            query="OpenViking 是什么？",
            context=["OpenViking 是上下文数据库..."],
            response="OpenViking 是 AI Agent 数据库",
            ground_truth="OpenViking 是开源上下文数据库"
        ),
    ]
    dataset = EvalDataset(name="eval", samples=samples)
    
    # 运行评估（可配置性能参数）
    evaluator = RagasEvaluator(
        max_workers=8,      # 并发数
        batch_size=5,       # 批处理大小
        timeout=120,        # 超时时间（秒）
        max_retries=2,      # 最大重试次数
    )
    summary = await evaluator.evaluate_dataset(dataset)
    
    # 输出结果
    for metric, score in summary.mean_scores.items():
        print(f"{metric}: {score:.2f}")

asyncio.run(main())
```

### 示例 2：CLI 工具评估

```bash
# 基础评估
python -m openviking.eval.rag_eval \
    --docs_dir ./docs \
    --question_file ./questions.jsonl \
    --output ./results.json

# 启用 RAGAS 指标
python -m openviking.eval.rag_eval \
    --docs_dir ./docs \
    --question_file ./questions.jsonl \
    --ragas \
    --output ./results.json

# 启用 IO 录制（用于存储层评估）
python -m openviking.eval.rag_eval \
    --docs_dir ./docs \
    --question_file ./questions.jsonl \
    --recorder \
    --output ./results.json
```

### 示例 3：基于本仓库的评估

在 OpenViking 仓库根目录下执行：

```bash
# 评估文档检索效果
python -m openviking.eval.rag_eval \
    --docs_dir ./docs \
    --docs_dir ./README.md \
    --question_file ./openviking/eval/datasets/local_doc_example_glm5.jsonl \
    --output ./eval_results.json
```

## 存储层评估

### IO Recorder 录制器

IO Recorder 用于录制评估过程中的所有 IO 操作（FS、VikingDB），记录请求参数、响应结果、耗时等信息。

```python
from openviking.storage.recorder import init_recorder, get_recorder

# 初始化录制器
init_recorder(enabled=True)

# 进行评估操作...
# 操作会自动记录到 ./records/io_recorder_YYYYMMDD.jsonl

# 获取统计信息
recorder = get_recorder()
stats = recorder.get_stats()
print(f"Total operations: {stats['total_count']}")
print(f"FS operations: {stats['fs_count']}")
print(f"VikingDB operations: {stats['vikingdb_count']}")
```

### Playback 回放器

Playback 用于回放录制的 IO 操作，对比不同存储后端的性能差异。

```bash
# 查看记录统计
uv run play_recorder.py \
    --record_file ./records/io_recorder_20260214.jsonl \
    --stats-only

# 使用远程配置回放
uv run play_recorder.py \
    --record_file ./records/io_recorder_20260214.jsonl \
    --config_file ./ov-remote.conf \
    --output ./playback_results.json

# 过滤特定操作类型
uv run play_recorder.py \
    --record_file ./records/io_recorder_20260214.jsonl \
    --config_file ./ov.conf \
    --io-type fs \
    --operation read
```

### 存储层评估流程

1. **录制阶段**：使用 `--recorder` 参数运行评估，记录所有 IO 操作
2. **回放阶段**：使用不同的配置文件回放，对比性能差异
3. **分析结果**：查看各操作的耗时对比，识别性能瓶颈

```bash
# 步骤 1：使用本地存储录制
python -m openviking.eval.rag_eval \
    --docs_dir ./docs \
    --question_file ./questions.jsonl \
    --recorder \
    --config ./ov-local.conf

# 步骤 2：使用远程存储回放
uv run play_recorder.py \
    --record_file ./records/io_recorder_20260215.jsonl \
    --config_file ./ov.conf

# 步骤 3：对比分析
# 输出会显示各操作的原始耗时 vs 回放耗时
```

## 评估指标

### RAGAS 指标

| 类别 | 指标 | 说明 |
|------|------|------|
| 检索质量 | context_precision | 上下文精确度 |
| | context_recall | 上下文召回率 |
| 生成质量 | faithfulness | 答案忠实度 |
| | answer_relevance | 答案相关性 |

### 性能指标

| 指标 | 说明 |
|------|------|
| retrieval_time | 检索耗时 |
| total_latency | 端到端延迟 |

### 存储层指标

| 操作类型 | 说明 |
|----------|------|
| fs.read | 文件读取 |
| fs.write | 文件写入 |
| fs.ls | 目录列表 |
| fs.stat | 文件信息 |
| fs.tree | 目录树遍历 |
| vikingdb.search | 向量搜索 |
| vikingdb.upsert | 向量写入 |
| vikingdb.filter | 标量过滤 |

## RAGAS 性能配置

RAGAS 评估支持以下性能配置参数：

| 参数 | 默认值 | 环境变量 | 说明 |
|------|--------|----------|------|
| max_workers | 16 | RAGAS_MAX_WORKERS | 并发 worker 数量 |
| batch_size | 10 | RAGAS_BATCH_SIZE | 批处理大小 |
| timeout | 180 | RAGAS_TIMEOUT | 超时时间（秒） |
| max_retries | 3 | RAGAS_MAX_RETRIES | 最大重试次数 |

```bash
# 通过环境变量配置
export RAGAS_MAX_WORKERS=8
export RAGAS_BATCH_SIZE=5
export RAGAS_TIMEOUT=120
export RAGAS_MAX_RETRIES=2

python -m openviking.eval.rag_eval --docs_dir ./docs --question_file ./questions.jsonl --ragas
```

## 相关文件

- CLI 工具：[rag_eval.py](./rag_eval.py)
- RAGAS 集成：[ragas.py](./ragas.py)
- IO 录制器：[storage/recorder/__init__.py](../storage/recorder/__init__.py)
- 回放器：[storage/recorder/playback.py](../storage/recorder/playback.py)
- 回放 CLI：[play_recorder.py](../../play_recorder.py)
- 示例数据：[datasets/local_doc_example_glm5.jsonl](./datasets/local_doc_example_glm5.jsonl)
- 测试文件：[tests/eval/](../../tests/eval/)、[tests/storage/test_recorder.py](../../tests/storage/test_recorder.py)
