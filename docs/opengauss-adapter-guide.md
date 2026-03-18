# OpenGauss 向量库后端接入指南

本文档基于 PR #521 (OceanBase适配) 分析，总结接入 openGauss 需要的改动内容。

---

## 一、参考 PR 分析 (OceanBase)

### 1.1 文件改动清单

| 文件 | 类型 | 行数 | 作用 |
|------|------|------|------|
| `openviking/storage/vectordb_adapters/oceanbase_adapter.py` | 新增 | 556行 | 核心适配器实现 |
| `openviking/storage/vectordb_adapters/factory.py` | 修改 | +2行 | 注册新后端 |
| `openviking/storage/vectordb_adapters/__init__.py` | 修改 | +2行 | 导出适配器 |
| `openviking_cli/utils/config/vectordb_config.py` | 修改 | +31行 | 配置模型 |
| `pyproject.toml` | 修改 | +3行 | 依赖声明 |
| `docs/zh/guides/06-oceanbase-integration.md` | 新增 | 274行 | 中文集成文档 |
| `docs/en/guides/06-oceanbase-integration.md` | 新增 | 274行 | 英文集成文档 |
| `docs/zh/concepts/05-storage.md` | 修改 | +1行 | 存储文档更新 |
| `docs/en/concepts/05-storage.md` | 修改 | +1行 | 存储文档更新 |
| `docs/zh/guides/01-configuration.md` | 修改 | +24行 | 配置文档更新 |
| `docs/en/guides/01-configuration.md` | 修改 | +26行 | 配置文档更新 |
| `tests/vectordb/test_oceanbase_live.py` | 新增 | 542行 | 集成测试 |

**总计**: 12个文件, +1733行, -3行

---

## 二、openGauss 接入需要修改的文件

### 2.1 必须新增的文件

| 文件路径 | 参考文件 | 预估行数 | 说明 |
|----------|----------|----------|------|
| `openviking/storage/vectordb_adapters/opengauss_adapter.py` | oceanbase_adapter.py | 500-600行 | 核心适配器 |
| `docs/zh/guides/07-opengauss-integration.md` | 06-oceanbase-integration.md | ~250行 | 中文文档 |
| `docs/en/guides/07-opengauss-integration.md` | 06-oceanbase-integration.md | ~250行 | 英文文档 |
| `tests/vectordb/test_opengauss_live.py` | test_oceanbase_live.py | ~400行 | 集成测试 |

### 2.2 需要修改的文件

| 文件路径 | 修改内容 |
|----------|----------|
| `openviking/storage/vectordb_adapters/factory.py` | 注册 `"opengauss": OpenGaussCollectionAdapter` |
| `openviking/storage/vectordb_adapters/__init__.py` | 导出 `OpenGaussCollectionAdapter` |
| `openviking_cli/utils/config/vectordb_config.py` | 新增 `OpenGaussConfig` 配置类 |
| `pyproject.toml` | 添加 `opengauss` 可选依赖组 |
| `docs/zh/concepts/05-storage.md` | 添加 openGauss 后端说明 |
| `docs/en/concepts/05-storage.md` | 添加 openGauss 后端说明 |
| `docs/zh/guides/01-configuration.md` | 添加 openGauss 配置示例 |
| `docs/en/guides/01-configuration.md` | 添加 openGauss 配置示例 |

---

## 三、核心代码实现指南

### 3.1 适配器结构 (`opengauss_adapter.py`)

```python
# 文件结构
opengauss_adapter.py
├── 导入依赖 (psycopg2, sqlalchemy 等)
├── 辅助函数
│   ├── _openviking_field_to_column()    # 字段类型映射
│   ├── _build_create_table_sql()        # 建表SQL生成
│   └── _distance_to_operator()          # 距离度量映射
├── OpenGaussCollection(ICollection)     # 集合实现类
│   ├── __init__()
│   ├── _filter_to_where()               # 过滤条件转SQL
│   ├── search_by_vector()               # 向量检索
│   ├── search_by_scalar()               # 标量排序
│   ├── search_by_random()               # 随机检索
│   ├── upsert_data()                    # 数据写入
│   ├── fetch_data()                     # 数据读取
│   ├── delete_data()                    # 数据删除
│   ├── aggregate_data()                 # 聚合统计
│   ├── create_index()                   # 创建索引
│   ├── drop_index()                     # 删除索引
│   └── ... (其他 ICollection 方法)
└── OpenGaussCollectionAdapter(CollectionAdapter)  # 适配器类
    ├── from_config()                    # 从配置创建
    ├── _load_existing_collection_if_needed()  # 加载已有集合
    └── _create_backend_collection()     # 创建新集合
```

### 3.2 ICollection 必须实现的方法

```python
class OpenGaussCollection(ICollection):
    # 集合管理
    def update(self, fields, description): ...
    def get_meta_data(self) -> Dict: ...
    def close(self): ...
    def drop(self): ...
    
    # 索引管理
    def create_index(self, index_name, meta_data): ...
    def has_index(self, index_name) -> bool: ...
    def get_index(self, index_name): ...
    def list_indexes(self) -> List[str]: ...
    def drop_index(self, index_name): ...
    def update_index(self, index_name, scalar_index, description): ...
    def get_index_meta_data(self, index_name) -> Dict: ...
    
    # 向量检索
    def search_by_vector(self, index_name, dense_vector, limit, offset, filters, sparse_vector, output_fields) -> SearchResult: ...
    def search_by_keywords(self, index_name, keywords, query, limit, offset, filters, output_fields) -> SearchResult: ...
    def search_by_id(self, index_name, id, limit, offset, filters, output_fields) -> SearchResult: ...
    def search_by_multimodal(self, index_name, text, image, video, limit, offset, filters, output_fields) -> SearchResult: ...
    def search_by_random(self, index_name, limit, offset, filters, output_fields) -> SearchResult: ...
    def search_by_scalar(self, index_name, field, order, limit, offset, filters, output_fields) -> SearchResult: ...
    
    # 数据操作
    def upsert_data(self, data_list, ttl): ...
    def fetch_data(self, primary_keys) -> FetchDataInCollectionResult: ...
    def delete_data(self, primary_keys): ...
    def delete_all_data(self): ...
    
    # 聚合
    def aggregate_data(self, index_name, op, field, filters, cond) -> AggregateResult: ...
```

### 3.3 CollectionAdapter 必须实现的方法

```python
class OpenGaussCollectionAdapter(CollectionAdapter):
    @classmethod
    def from_config(cls, config) -> "OpenGaussCollectionAdapter":
        """从配置创建适配器实例"""
        ...
    
    def _load_existing_collection_if_needed(self) -> None:
        """懒加载已存在的集合"""
        ...
    
    def _create_backend_collection(self, meta: Dict) -> Collection:
        """创建后端集合并返回 Collection 包装"""
        ...
```

---

## 四、配置模型

### 4.1 新增 OpenGaussConfig (`vectordb_config.py`)

```python
class OpenGaussConfig(BaseModel):
    """Configuration for openGauss vector database (via psycopg2 + pgvector)."""
    
    host: str = Field(
        default="127.0.0.1",
        description="openGauss host address",
    )
    port: int = Field(
        default=5432,
        description="openGauss port",
    )
    user: str = Field(
        default="gaussdb",
        description="Database user",
    )
    password: str = Field(
        default="",
        description="Database password",
    )
    db_name: str = Field(
        default="openviking",
        description="Database name",
    )
    
    model_config = {"extra": "forbid"}
```

### 4.2 修改 VectorDBBackendConfig

```python
class VectorDBBackendConfig(BaseModel):
    # ... 现有字段 ...
    
    # 新增 openGauss 配置
    opengauss: Optional[OpenGaussConfig] = Field(
        default_factory=lambda: OpenGaussConfig(),
        description="openGauss configuration for 'opengauss' type",
    )
    
    @model_validator(mode="after")
    def validate_config(self):
        standard_backends = ["local", "http", "volcengine", "vikingdb", "oceanbase", "opengauss"]
        # ...
        elif self.backend == "opengauss":
            if not self.opengauss:
                raise ValueError("VectorDB opengauss backend requires 'opengauss' config")
            if not self.opengauss.host:
                raise ValueError("VectorDB opengauss backend requires 'opengauss.host' to be set")
        return self
```

---

## 五、工厂注册

### 5.1 修改 factory.py

```python
from .opengauss_adapter import OpenGaussCollectionAdapter

_ADAPTER_REGISTRY: dict[str, type[CollectionAdapter]] = {
    "local": LocalCollectionAdapter,
    "http": HttpCollectionAdapter,
    "volcengine": VolcengineCollectionAdapter,
    "vikingdb": VikingDBPrivateCollectionAdapter,
    "oceanbase": OceanBaseCollectionAdapter,
    "opengauss": OpenGaussCollectionAdapter,  # 新增
}
```

### 5.2 修改 __init__.py

```python
from .opengauss_adapter import OpenGaussCollectionAdapter

__all__ = [
    # ... 现有导出 ...
    "OpenGaussCollectionAdapter",
]
```

---

## 六、依赖配置

### 6.1 修改 pyproject.toml

openGauss 内置向量类型和向量索引，无需 pgvector 扩展，也无需 `pgvector` Python 包。
驱动使用 openGauss 官方 psycopg2 连接器：

```toml
[project.optional-dependencies]
# ... 现有依赖 ...

opengauss = []  # 无额外 Python 依赖；驱动单独安装，见下
```

驱动安装方式：

```bash
pip install git+https://gitcode.com/opengauss/openGauss-connector-python-psycopg2.git
```

---

## 七、关键实现细节

### 7.1 字段类型映射

| OpenViking FieldType | PostgreSQL/openGauss 类型 |
|---------------------|---------------------------|
| `string` / `path` | `VARCHAR(4096)` |
| `int64` | `BIGINT` |
| `vector` | `vector(dim)` |
| `sparse_vector` | 不支持 (可用 JSONB 模拟) |
| `date_time` | `BIGINT` (时间戳) |

### 7.2 距离度量映射

| OpenViking distance | pgvector 操作符 |
|--------------------|-----------------|
| `l2` | `<->` (L2 distance) |
| `ip` | `<#>` (inner product) |
| `cosine` | `<=>` (cosine distance) |

### 7.3 过滤语法转换

```python
def _filter_to_where(self, filters: Dict) -> str:
    """
    OpenViking Filter → SQL WHERE
    
    示例转换:
    {"op": "must", "field": "account_id", "conds": ["acc1"]}
    → "account_id IN ('acc1')"
    
    {"op": "range", "field": "timestamp", "gte": 1000, "lt": 2000}
    → "timestamp >= 1000 AND timestamp < 2000"
    
    {"op": "and", "conds": [...]}
    → "(cond1) AND (cond2)"
    """
```

### 7.4 向量检索 SQL 示例

```sql
-- 向量相似度检索
SELECT id, uri, content, vector <-> %s AS distance
FROM context
WHERE account_id = %s
ORDER BY distance
LIMIT %s OFFSET %s;

-- 创建 HNSW 索引
CREATE INDEX idx_vector_hnsw ON context 
USING hnsw (vector vector_l2_ops)
WITH (m = 16, ef_construction = 64);
```

---

## 八、测试要点

### 8.1 必须覆盖的测试场景

- [ ] 后端工厂路由正确
- [ ] 集合生命周期 (exists/create/drop)
- [ ] 基础数据链路 (upsert/get/delete/query)
- [ ] 向量检索正确性
- [ ] count/aggregate 行为
- [ ] filter 条件生效 (含组合条件)
- [ ] 索引创建与删除

### 8.2 Docker 测试环境

```bash
# 启动 openGauss（内置向量支持，无需额外扩展）
docker run -d --name opengauss \
  -p 5432:5432 \
  -e GS_PASSWORD=Gauss@123 \
  enmotech/opengauss:latest

# 创建数据库
docker exec -it opengauss gsql -d postgres -U gaussdb -c "CREATE DATABASE openviking;"
```

---

## 九、与 OceanBase 实现的主要差异

| 方面 | OceanBase | openGauss |
|------|-----------|-----------|
| SDK | `pyobvector` (Milvus-like) | `psycopg2` (原生SQL) |
| 向量检索 | `client.search()` | `SELECT ... ORDER BY vec <-> query` |
| 索引创建 | `client.create_index()` | `CREATE INDEX USING hnsw` |
| 过滤条件 | SQLAlchemy ORM | 原生 SQL WHERE |
| 稀疏向量 | 原生支持 | 需自行实现 (可选) |

---

## 十、实现优先级建议

1. **P0 - 核心功能**
   - [ ] `opengauss_adapter.py` 基础实现
   - [ ] 配置模型与工厂注册
   - [ ] 向量检索 (dense)
   - [ ] 基础 CRUD

2. **P1 - 完善功能**
   - [ ] 过滤语法完整支持
   - [ ] 索引管理
   - [ ] 集成测试

3. **P2 - 文档与优化**
   - [ ] 中英文集成文档
   - [ ] 性能优化
   - [ ] 稀疏向量支持 (可选)

---

## 十一、参考资源

- [OpenViking VectorDB Adapter 接入指南](../openviking/storage/vectordb_adapters/README.md)
- [PR #521 OceanBase 实现](https://github.com/volcengine/OpenViking/pull/521)
- [pgvector 官方文档](https://github.com/pgvector/pgvector)
- [openGauss 官方文档](https://opengauss.org/)
