# VectorDB Adapter 接入指南（新增第三方后端）

本指南说明如何在 `openviking/storage/vectordb_adapters` 下新增一个第三方向量库后端，并接入 OpenViking 现有检索链路。

---

## 1. 目标与范围

### 目标
- 以最小改动新增一个向量库后端。
- 保持上层业务接口不变（`find/search` 等无需改调用方式）。
- 将后端差异封装在 Adapter 层，不泄漏到业务层。

### 非目标
- 不改上层语义检索策略（租户、目录层级、召回策略）。
- 不增加新的对外 API 协议。

---

## 2. 架构位置与职责

当前分层职责如下：

1. **上层语义层（OpenViking 业务）**  
   面向语义接口，不关心后端协议差异。

2. **通用向量存储层（Store/Backend）**  
   提供统一查询、写入、删除、计数能力。

3. **Adapter 层（本目录）**  
   负责把统一能力映射到具体后端实现（local/http/volcengine/vikingdb/thirdparty）。

新增后端时，主要只改第 3 层。

---

## 3. 接入前提

在开始前，请确认：

- 你已拿到第三方后端的：
  - 集合管理 API（查/建/删集合）
  - 数据 API（upsert/get/delete/search/aggregate）
- 你已明确该后端的：
  - 认证方式（AK/SK、token、header）
  - 过滤语法能力（是否支持 must/range/and/or）
  - 索引参数约束（dense/sparse、距离度量、索引类型）

---

## 4. 接入步骤

## Step 1：新增 Adapter 文件

在目录下新增文件，例如：

- `openviking/storage/vectordb_adapters/thirdparty_adapter.py`

定义类：

- `ThirdPartyCollectionAdapter(CollectionAdapter)`

基类位于：

- `openviking/storage/vectordb_adapters/base.py`

---

## Step 2：实现最小必需方法

你需要实现以下方法：

1. `from_config(cls, config)`  
   - 从 `VectorDBBackendConfig` 读取后端配置并构造 adapter。
   - collection 名建议使用 `config.name or "context"`。

2. `_load_existing_collection_if_needed(self)`  
   - 懒加载已存在 collection handle。
   - 若不存在，保持 `_collection is None`。

3. `_create_backend_collection(self, meta)`  
   - 按传入 schema 创建 collection 并返回 handle。

---

## Step 3：按后端能力补充可选 Hook

如后端有差异，可重写：

- `_sanitize_scalar_index_fields(...)`
- `_build_default_index_meta(...)`

若后端支持服务端全文检索（BM25 grep）并需要写入 `content` 全文字段，设置类属性：

- `USE_CONTENT_FIELD = True`（默认为 `False`）

默认 `False` 时，写入时会自动跳过 `content` 字段。VikingDB 系适配器静态启用；openGauss 在显式正文迁移后根据实际集合状态动态启用，不因配置声明就自动执行迁移。

目的：把后端特性差异收敛在 adapter 内。

---

## Step 4：注册到 Factory

编辑：

- `openviking/storage/vectordb_adapters/factory.py`

在 `_ADAPTER_REGISTRY` 增加映射，例如：

```python
"thirdparty": ThirdPartyCollectionAdapter
```

这样 `create_collection_adapter(config)` 会自动路由到你的实现。

---

## Step 5：补充配置模型

确保配置中可声明新 backend（如 `backend: thirdparty`）及其专属字段（endpoint/auth/region 等）。

原则：
- `create_collection` 时使用配置中的 name 绑定 collection。
- 后续操作默认绑定，不需要每次传 collection_name。

---

## Step 6：配置 ov.conf

对于没有提交到仓库，或者在第三方仓库的 Adapter，可以通过配置 `backend` 为完整的类路径来动态加载。
同时，可以使用 `custom_params` 字段传递自定义参数。

在 `ov.conf`  中添加如下配置：

```json
{
  "storage": {
    "vectordb": {
      "backend": "tests.storage.mock_backend.MockCollectionAdapter",
      "name": "mock_test_collection",
      "custom_params": {
        "custom_param1": "val1",
        "custom_param2": 123
      }
    }
  }
}
```

注意：
1. `backend`: 填写 Adapter 类的完整 Python 路径（例如 `my_project.adapters.MyAdapter`）。
2. `custom_params`: 这是一个字典，你可以放入任何自定义参数，Adapter 的 `from_config` 方法可以通过 `config.custom_params` 获取这些值。



---

## 5. Filter 与查询兼容规则

- Adapter 需要兼容统一过滤表达。
- 上层传入的过滤表达会经由统一编译流程进入后端查询。
- 若第三方语法不同，请在 adapter 内做映射，不改上层调用协议。

关键原则：
- **后端 DSL 不上浮到业务层**。
- **业务层不依赖第三方私有查询语法**。

---

## 6. 最小代码骨架（示例）

```python
from __future__ import annotations
from typing import Any, Dict

from openviking.storage.vectordb_adapters.base import CollectionAdapter

class ThirdPartyCollectionAdapter(CollectionAdapter):
    def __init__(self, *, endpoint: str, token: str, collection_name: str):
        super().__init__(collection_name=collection_name)
        self.mode = "thirdparty"
        self._endpoint = endpoint
        self._token = token

    @classmethod
    def from_config(cls, config: Any):
        if not config.thirdparty or not config.thirdparty.endpoint:
            raise ValueError("ThirdParty backend requires endpoint")
        return cls(
            endpoint=config.thirdparty.endpoint,
            token=config.thirdparty.token,
            collection_name=config.name or "context",
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        # TODO: 查询远端 collection 是否存在，存在则初始化 handle
        # self._collection = ...
        pass

    def _create_backend_collection(self, meta: Dict[str, Any]):
        # TODO: 调后端 create collection，并返回 collection handle
        # return ...
        raise NotImplementedError
```

---

## 7. 测试要求（必须）

至少覆盖以下场景：

1. backend 工厂路由正确（能创建到新 adapter）。
2. collection 生命周期可用（exists/create/drop）。
3. 基础数据链路可用（upsert/get/delete/query）。
4. count/aggregate 行为正确。
5. filter 条件可正确生效（含组合条件）。

---

## 8. 常见问题与排查

### Q1：启动时报 backend 不支持
- 检查 factory 是否注册。
- 检查配置里的 backend 字符串是否与 registry key 一致。

### Q2：集合创建成功但查询为空
- 检查 collection 绑定名是否一致。
- 检查索引是否创建成功。
- 检查 filter 映射是否把条件误转成空条件。

### Q3：count 与 query 条数不一致
- 检查 aggregate API 的字段命名与返回结构解析。
- 检查 count 使用的 filter 与 query 使用的 filter 是否一致。

---

## 9. 验收标准

当满足以下条件，即可视为接入完成：

- `backend=thirdparty` 可正常初始化。
- create 后可完成 upsert/get/query/delete/count 全流程。
- 不改上层业务调用方式即可参与 `find/search` 检索链路。
- 后端差异全部封装在 adapter 层。

## 10. openGauss 检索扩展的启用与验收

新能力默认关闭，旧 Dense 配置不要求迁移。`opengauss` 配置新增：

- `fulltext_engine`: `disabled`（默认）、`bm25` 或 `fts`。`fts` 使用 GIN/ts_rank，不是 BM25；显式 BM25 请求不会回退为 FTS。
- `text_search_config`: 默认 `simple`；中文需要运维预先安装并配置经验证的分词器，不自动安装扩展。
- `sparse_enabled`: 默认 `false`。开启后仍需显式创建 posting 索引；`sparse_weight > 0` 要求该开关开启。
- `candidate_limit`: 默认 1000、最大 10000，每路候选预算；Hybrid 与时间衰减属于有界近似排序，最终分页窗口不能超过此预算。

配置不自动启动 DDL、回填或删除。使用已绑定集合的 adapter 显式操作：

1. 在独立只读连接上调用 `probe_fulltext_capabilities(conn)`；该入口不初始化元数据表。已有 adapter 的 `probe_capabilities()` 可用于管理诊断，但 adapter 构造本身仍有原有初始化行为。
2. `adapter.prepare_retrieval(allow_ddl=True)`：检查所选全文引擎、添加 nullable `content TEXT`，并按配置显式建立 Sparse postings。
3. `collection = adapter.retrieval_collection()`。循环 `collection.backfill_content_batch(loader, batch_size=100)`，直到 `done=True`；loader 返回既有源存储文本，不调用 LLM/embedding。默认 snapshot 包含 `id`、`uri`、`updated_at`，可通过 `source_fields` 增加层级/类型信息。调用方必须按记录级别选择正文来源，并保证源变更推进 `updated_at`。
4. `collection.build_fulltext_index(allow_ddl=True)`：验证无 NULL 正文，构建并校验物理索引后标记 ready。索引重建阻塞写入，不使用 CONCURRENTLY；需安排维护窗口。
5. 按租户验证 `search_by_keywords(..., mode='bm25'/'fts', fields=['content'])` 后，再启用 `/api/v1/search/search` 的 `search_type='keywords'`、`mode='list'`。关键词响应为独立的 `{records, engine, ready, candidate_complete}`，不冒充语义 FindResult；本分支未支持关键词 context、会话规划、read_content/provenance 和 score_threshold 组合，均明确拒绝。

`backfill_content()` 可对显式 ID 批次读取源文本，使用带账户、URI、updated_at、content IS NULL 条件的原子更新，不覆盖已有正文、不创建新记录。集合级回填检查点持久化，冲突不推进检查点；这是独立管理接口，未在旧分支不存在的持久 reindex 队列中注册新任务类型。

Sparse 使用数据库内 posting 表和词项索引；文档与查询权重须有限、非负，最多 4096 个非零项，词项最多 256 UTF-8 字节。纯 Sparse 仅返回正点积重叠记录；Hybrid 独立召回两路并补齐候选分数，再执行 alpha 融合，不是 Dense+原文 BM25。基表写入、更新、删除必须经适配器，直接 SQL 不会自动维护 postings。

事件时间衰减通过公开 find/search(list) 的 `events_time_decay_protection` 启用，使用统一请求时间和固定 7 天半衰期，仅针对 `context_type=memory` 且带 `memory_type=events` 标签的记录。本旧分支仅 openGauss QUICK 路径接入，THINKING/递归和 context 模式明确拒绝，不静默忽略。未来/缺失时间和负分遵循共享评分契约。

限量、简单文本 grep 可优先检查全文候选；只有找到足够真实匹配时提前返回。候选不足、空召回、复杂正则、无限量、投影/标签约束等保持完整 FS 路径；`candidate_complete=false` 不能被当作任意 regex 的无命中证明。

真实数据库验收采用 `OPENVIKING_OPENGAUSS_FEATURE_DSN`，必须指向可创建和删除测试集合的隔离实例。运行 `tests/vectordb/test_opengauss_retrieval_database.py`，包含全文回填/重建、过滤、索引计划、Sparse 更新删除与衰减分页。未配置时明确 skip，不视为验收通过。

上线顺序：隔离实例验证 → 回填/重建演练 → 按租户影子查询 → 小流量 → 放量。检查候选量、回填检查点、错误与 FS 回退原因；测量 Recall@K、P95/P99、QPS、写入放大和索引体积。关闭新全文查询可调用 `configure_fulltext('disabled')`；回滚保留原集合和 Dense 索引，不删除业务数据。主备切换、复制延迟、过滤执行计划和性能门槛必须在目标环境实测；SPQ 新增全文/Sparse 仍明确拒绝，后续单独认证。