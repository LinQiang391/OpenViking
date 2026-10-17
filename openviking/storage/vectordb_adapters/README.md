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

默认 `False` 时，写入时会自动跳过 `content` 字段（该字段仅 VikingDB 系后端使用），新增后端无需额外代码。

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

## 10. openGauss 检索扩展与正文迁移

本轮基于 `origin/main` 的 `6006fc3403b52f52ef8c01f128691b5a10a01e99` 适配，保留主线 `search_type="keywords"` 的 find/search/context 和 FindResult 协议，不新增独立的 records HTTP 协议。新能力默认关闭，原 Dense-only 配置不要求迁移。

新增行为按当前账户实际解析的后端隔离：只有 openGauss 每次校验物理 BM25 就绪状态、限制关键词参数、物化完整可见正文并对预载正文取消提前截断。VikingDB/Volcengine 保留原全文缓存、关键词参数透传、正文来源/失败语义、预载长度和 grep 路由；memory 限定的事件衰减及关键词加衰减的显式拒绝也只用于 openGauss。`content_only` 在创建任务或入队前独立校验原子正文回填能力，不支持的后端立即拒绝；原 `vectors_only`、`semantic_and_vectors` 流程不增加该检查。

### 显式启用

`storage.vectordb.opengauss` 提供 `fulltext_engine`（仅 `disabled`、`bm25`）、`sparse_enabled`（默认 false）和 `candidate_limit`（默认 1000，最大 10000）。全文检索只使用原生 BM25；模型 Sparse 的融合权重仍使用现有 `sparse_weight`，不等于 Dense+BM25 融合。

1. 在空闲连接调用只读 `probe_fulltext_capabilities()`，确认原生 BM25 能力；目录存在不代表实际索引计划或特定表存储已认证。探测不会安装扩展，不提供其他全文引擎降级。
2. 通过适配器 `retrieval_collection()` 管理句柄显式执行 `configure_fulltext("bm25")` 和 `migrate_content_schema(allow_ddl=True)`。也可按配置使用 `prepare_retrieval(allow_ddl=True)`；它不自动回填正文或把全文索引标记为就绪。
3. 使用现有 `/api/v1/content/reindex`，设置 `mode="content_only"` 回填正文。默认只填缺失正文；设置 `force=true` 才会按账户、URI、updated_at、原正文条件修复历史非 NULL 摘要。正文迁移不调用 Embedding/LLM、不新建记录、不执行隐式 DDL、不重写向量。
4. 明确确认正文覆盖后，调用 `build_fulltext_index(allow_ddl=True)`。物理索引校验通过才发布 ready；重建使用 `rebuild_fulltext_index(allow_ddl=True)`。
5. Sparse 需显式 `create_sparse_index(candidate_limit=..., sparse_weight=...)`，或使用上述配置管理入口。日常基表写删必须经过适配器，直接 SQL 不维护 postings 或覆盖状态。

schema v2 使用内部 `_ov_content_empty` 标记区分合法空源与缺失正文，兼容 A 模式空字符串折叠为 NULL；内部标记不属于公共记录字段。升级 schema v1 必须重新执行显式迁移。全文 ready 时，适配器在同一写事务校验受影响记录，拒绝缺正文的新记录和清空为未知正文的更新，整批回滚；部分更新不修改正文时保留原值。源读取返回 None/非法类型或抛错不视为成功空源。在线正文与回填采用同一解析策略，Embedding 文本仍可独立使用摘要。记忆命名空间的正文在后端限长之前统一经过 `visible_content()`，去除末尾保留的 `MEMORY_FIELDS` 元数据尾注；预载 `_materialized_content` 也遵循同一规则，资源和技能的同形注释不删除。历史已索引隐藏尾注的非 NULL 正文不会被缺失回填自动修复，应在正确账户和 URI 范围显式执行 `mode="content_only", force=true`；这会通过 CAS 更新正文并由数据库维护现有全文索引，不改向量。

### 恢复与查询边界

- content-only 复用主线持久 QueueFS 根消息、Task、锁租约、身份及去重；`wait=true` 同样持久入队。检查点存于持久 Task 元数据并绑定账户、用户、URI、模式与解析版本；CAS 冲突和源错误不推进游标，重新投递刷新失败记录。正文失败最多自动重投三次，耗尽后任务失败并保留检查点，可修复源后重新提交安全扫描。提交正文后、检查点落盘前崩溃可能使计数少记一次，但重放不覆盖新版本或复活删除记录。
- 持久正文扫描仅接受 OV 标准的 32 位小写十六进制记录 ID，其他 ID 明确拒绝，避免数据库排序规则与 Python 顺序不一致造成跳项；原始集合级回填仍使用 C-collation 检查点。并发新增的早于游标的记录由后续独立扫描覆盖，单次迁移不是全库静止快照。
- BM25 保留原生单排序有序索引扫描，在单次 REPEATABLE READ 快照中扩展候选前缀，取得完整分页边界同分组后按分数、ID 截页。最多 100000 个候选，无法证明边界完整时明确报错，不返回不稳定页。不同分页请求不共享快照；原生 BM25 语料统计也不保证在并发写入时不变。
- 公共 `keywords` 查询仅在 BM25 正文与物理索引就绪时执行。不支持的实例、旧引擎配置或非 BM25 索引明确拒绝，不隐式降级。旧配置应删除 `text_search_config`，将 `fulltext_engine` 改为 `bm25`；旧 FTS 索引须由管理员确认后显式处理，适配器不自动转换或删除它。
- 模型 Sparse 使用数据库 posting 表与词项点积，不是 Sparse ANN。权重须有限且非负，最多 4096 个非零项、每词项最多 256 UTF-8 字节。Hybrid 两路分别过滤召回、补齐候选实际分数再融合，每路最多 candidate_limit，非全表 Python 扫描，但不保证全库精确融合 Top-K。
- 事件衰减沿用主线固定 7 天半衰期和请求时钟，只针对 memory 且含 `memory_type=events` 的记录。接通主线 QUICK、THINKING 与 context 实际检索链，保留已有 rerank 语义；本主线已经没有旧式递归检索实现，不另造递归链。
- 限量简单文本 grep 可优先验证全文候选，只有满足真实匹配数量才提前返回；不足、错误、复杂正则或投影等回退 FS。`candidate_complete=false` 不能作为任意 regex 无匹配的证明。
- 新全文/Sparse/高级排序的 SPQ 路径明确拒绝；主备切换和生产性能 SLA 尚未认证。

### 可复现验证

本机实库用例仅允许专用容器 `ov-retrieval-acceptance-20261016` 的 `127.0.0.1:35436/postgres`，每次使用随机隔离 schema/集合，结束清理本次对象，不修改业务实例或全局配置。凭据从该容器内存读取，不出现在命令参数或代码中：

- `.venv-opengauss-validation/bin/python tests/vectordb/test_opengauss_acceptance_helper.py tests/vectordb/test_opengauss_retrieval_database.py -q --no-cov -p no:cacheprovider`
- 同一入口运行 `test_opengauss_content_reindex_database.py`、`test_opengauss_acceptance_text.py` 或 `test_opengauss_acceptance_scale.py`。
- 覆盖 A/PG 空串与就绪不变量：设置 `OPENVIKING_CONTENT_COVERAGE_DOCKER=1` 运行 `test_opengauss_content_coverage.py`。
- 稳定分页及原生执行计划：设置 `OPENVIKING_OPENGAUSS_PAGINATION_CONTAINER=1` 运行 `test_opengauss_fulltext_pagination.py`。

验收区分结果正确、原生 BM25 索引路径可执行、代表性数据下优化器实际选择。缺少 DSN 的 skip、不支持的 RaBitQ，以及测试收集环境问题不能算通过。中文测试使用实际 BM25 索引与原生查询结果对照，不认证已经移除的其他全文引擎或 parser。

代表性规模测试使用 10000 行、8 维、七条路线（BM25、Dense、三种 Sparse 分布、Hybrid、Hybrid+衰减），每路 30 次串行及并发 4 的 32 次查询。断言选择性 BM25 查询使用索引、候选与独立基准一致、隔离对象清理无残留；Hybrid 精确对照仅限候选预算可覆盖的受控子集，不代表全库精确 Top-K，也不是生产 SLA。历史双引擎测试数字不能作为 BM25 单引擎版本的通过证明；真实 HTTP 服务也必须以 BM25 配置重新执行。全量测试及生产高可用尚未宣称通过。

关闭新增全文查询可用 `configure_fulltext("disabled")`；保留正文、原 Dense 索引和业务记录。重新放量前验证覆盖、错误率、FS 回退原因、候选量及代表性执行计划；主备/SPQ 需要单独的隔离认证环境。