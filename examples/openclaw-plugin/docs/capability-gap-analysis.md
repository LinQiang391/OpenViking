# OpenViking → OpenClaw 插件能力补全清单

> 基于 OpenViking 全量 HTTP API 与 `examples/openclaw-plugin` 已实现功能的对比分析。
>
> 生成时间：2026-04-07

---

## 总览

| 分类 | OpenViking 端点数 | 插件已对接 | 未对接 | 覆盖率 |
|------|-------------------|-----------|--------|--------|
| 资源 / 知识库导入 | 3 | 0 | **3** | 0% |
| 技能管理 | 1 | 0 | **1** | 0% |
| Pack 导入 / 导出 | 2 | 0 | **2** | 0% |
| 虚拟文件系统 | 6 | 2 | **4** | 33% |
| 内容读写 | 6 | 1 | **5** | 17% |
| 搜索与检索 | 4 | 1 | **3** | 25% |
| 关系图谱 | 3 | 0 | **3** | 0% |
| 会话管理 | 9 | 5 | **4** | 56% |
| 后台任务 | 2 | 1 | **1** | 50% |
| 统计 / 记忆健康 | 2 | 0 | **2** | 0% |
| 系统 / 运维 | 5 | 2 | **3** | 40% |
| 观测 / 调试 | 8 | 0 | **8** | 0% |
| 多租户管理 | 8 | 0 | **8** | 0% |
| Bot 代理 | 3 | 0 | **3** | 0% |

---

## 一、资源 / 知识库导入（完全缺失 ⚠️ 高优先级）

插件当前 **无法** 向 OpenViking 添加任何外部知识资源（文件、URL、目录）。这是最核心的能力缺口。

### 1.1 临时文件上传

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/resources/temp_upload` |
| **作用** | 上传本地文件到服务端临时区，返回 `temp_file_id`，供后续 `add-resource` 或 `pack/import` 使用 |
| **参数** | multipart `file`，可选 form `telemetry` |
| **插件现状** | ❌ 未实现 |
| **建议** | 在 `OpenVikingClient` 新增 `tempUpload(filePath)` 方法 |

### 1.2 添加资源（Add Resource）

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/resources` |
| **作用** | 将文件 / URL / 目录导入为知识库资源，OpenViking 自动分块、向量化、生成摘要 |
| **参数** | `path` 或 `temp_file_id`；`to` 或 `parent`（目标 viking:// URI）；`reason`、`instruction`（指导处理方式）；`wait`、`timeout`；`strict`；`ignore_dirs`、`include`、`exclude`（过滤规则）；`directly_upload_media`；`preserve_structure`；`watch_interval`（分钟级定期重新摄取） |
| **插件现状** | ❌ 未实现 |
| **建议** | 新增 tool `ov_add_resource`，允许 Agent 主动导入文档、网页、代码仓库等为知识源 |

### 1.3 添加技能资源（Add Skill）

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/skills` |
| **作用** | 注入结构化技能/指令数据，供检索与上下文组装使用 |
| **参数** | `data` 或 `temp_file_id`；`wait`、`timeout`、`telemetry` |
| **插件现状** | ❌ 未实现 |
| **建议** | 新增 tool `ov_add_skill` 或合并到资源导入工具 |

---

## 二、Pack 导入 / 导出（完全缺失）

OpenViking 支持 `.ovpack` 格式的知识库打包 / 迁移功能，插件未对接。

### 2.1 导出 Pack

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/pack/export` |
| **作用** | 将指定 `viking://` URI 路径打包为 `.ovpack` 文件 |
| **参数** | `uri`（源路径）、`to`（服务端导出目标路径） |
| **插件现状** | ❌ 未实现 |

### 2.2 导入 Pack

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/pack/import` |
| **作用** | 导入 `.ovpack` 文件（需先 `temp_upload`），可选重新向量化 |
| **参数** | `temp_file_id`、`parent`、`force`、`vectorize` |
| **插件现状** | ❌ 未实现 |
| **建议** | 与 temp_upload 组合，新增 `ov_pack_export` / `ov_pack_import` tool |

---

## 三、虚拟文件系统（部分缺失）

插件目前仅使用了 `ls`（目录浏览）和 `DELETE`（删除），尚缺以下操作。

### 3.1 目录树浏览

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/fs/tree` |
| **作用** | 返回 `viking://` 路径下的树形结构，支持层级限制 |
| **参数** | `uri`、`output`（`original` / `agent`）、`abs_limit`、`show_all_hidden`、`node_limit`、`level_limit` |
| **插件现状** | ❌ 未实现 |
| **建议** | 新增 tool `ov_tree`，帮助 Agent 全局浏览知识库结构 |

### 3.2 文件/节点元信息

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/fs/stat` |
| **作用** | 获取指定 URI 的元数据（大小、类型、创建时间等） |
| **参数** | `uri` |
| **插件现状** | ❌ 未实现 |

### 3.3 创建目录

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/fs/mkdir` |
| **作用** | 在 `viking://` 虚拟文件系统中创建目录 |
| **参数** | `uri` |
| **插件现状** | ❌ 未实现 |

### 3.4 移动 / 重命名

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/fs/mv` |
| **作用** | 移动或重命名知识库中的节点 |
| **参数** | `from_uri`、`to_uri` |
| **插件现状** | ❌ 未实现 |
| **建议** | 与 `memory_forget` 互补，新增 `ov_fs_manage` 或独立 tool |

---

## 四、内容读写（大部分缺失）

插件仅用了 `content/read`，OpenViking 的摘要层级、写入、下载和重索引能力均未对接。

### 4.1 摘要读取（Abstract / Overview）

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/content/abstract`（L0 摘要）<br>`GET /api/v1/content/overview`（L1 概览） |
| **作用** | 获取 OpenViking 对资源自动生成的多层级摘要，比 `read` 更精炼 |
| **参数** | `uri` |
| **插件现状** | ❌ 未实现 |
| **建议** | 高优先级——在 recall 结果中可用摘要替代全文，显著节省 token |

### 4.2 内容写入

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/content/write` |
| **作用** | 向指定 URI 写入或追加内容，自动触发向量化 |
| **参数** | `uri`、`content`、`mode`（`replace` / `append`）、`wait`、`timeout`、`telemetry` |
| **插件现状** | ❌ 未实现 |
| **建议** | 允许 Agent 直接创建/修改知识节点，不局限于 session commit 提取 |

### 4.3 文件下载

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/content/download` |
| **作用** | 下载资源的原始二进制文件 |
| **参数** | `uri` |
| **插件现状** | ❌ 未实现 |

### 4.4 重索引

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/content/reindex` |
| **作用** | 重新嵌入向量，可选 LLM 重新生成 L0/L1 摘要 |
| **参数** | `uri`、`regenerate`、`wait` |
| **插件现状** | ❌ 未实现 |
| **建议** | 在知识库维护场景中有用，可结合 `content/write` 使用 |

---

## 五、搜索与检索（大部分缺失）

插件仅使用了 `search/find`（语义检索），以下搜索方式未对接。

### 5.1 会话感知搜索

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/search/search` |
| **作用** | 与 `find` 类似但支持 `session_id`，可利用会话上下文做 re-ranking |
| **参数** | 同 `find` + `session_id` |
| **插件现状** | ❌ 未实现 |
| **建议** | 考虑在 `memory_recall` 中替换 `find` 为 `search`，提升相关性 |

### 5.2 正则搜索（Grep）

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/search/grep` |
| **作用** | 对知识库中的文本内容做正则匹配搜索 |
| **参数** | `uri`、`pattern`、`case_insensitive`、`node_limit` |
| **插件现状** | ❌ 未实现 |
| **建议** | 新增 tool `ov_grep`，允许精确文本搜索 |

### 5.3 Glob 模式搜索

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/search/glob` |
| **作用** | 按文件名/路径 glob 模式搜索知识库节点 |
| **参数** | `pattern`、`uri`、`node_limit` |
| **插件现状** | ❌ 未实现 |

---

## 六、关系图谱（完全缺失）

OpenViking 支持知识节点之间的关联关系管理，插件完全未对接。

### 6.1 查询关系

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/relations` |
| **参数** | `uri` |
| **插件现状** | ❌ 未实现 |

### 6.2 创建关系链接

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/relations/link` |
| **参数** | `from_uri`、`to_uris`（字符串或列表）、`reason` |
| **插件现状** | ❌ 未实现 |

### 6.3 删除关系链接

| 项 | 详情 |
|----|------|
| **端点** | `DELETE /api/v1/relations/link` |
| **参数** | `from_uri`、`to_uri` |
| **插件现状** | ❌ 未实现 |
| **建议** | 新增 `ov_relation` 工具或将关系操作嵌入到 store/forget 流程中 |

---

## 七、会话管理（部分缺失）

插件已对接 `addMessage`、`commit`、`getSession`、`getSessionContext`、`getSessionArchive`，但以下端点未对接。

### 7.1 创建会话

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/sessions` |
| **作用** | 显式创建新 OV 会话（初始化 user/agent 目录） |
| **插件现状** | ❌ 未实现（插件通过 `addMessage` 隐式创建） |
| **建议** | 在会话生命周期管理场景下有价值 |

### 7.2 列出会话

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/sessions` |
| **作用** | 列出所有已有会话 |
| **插件现状** | ❌ 未实现 |
| **建议** | 可用于 Agent 主动浏览历史会话上下文 |

### 7.3 记忆提取

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/sessions/{id}/extract` |
| **作用** | 手动触发记忆提取（独立于 commit 的提取过程） |
| **插件现状** | ❌ 未实现 |

### 7.4 使用记录

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/sessions/{id}/used` |
| **作用** | 记录 Agent 实际使用了哪些 context / skill，用于改进检索排序 |
| **参数** | `contexts`、`skill` |
| **插件现状** | ❌ 未实现 |
| **建议** | 高优先级——可闭环 recall → use → feedback 循环，提升知识检索质量 |

### 7.5 删除会话

| 项 | 详情 |
|----|------|
| **端点** | `DELETE /api/v1/sessions/{id}` |
| **插件现状** | ⚠️ Client 中已定义 `deleteSession()` 但 **未被任何代码调用** |
| **建议** | 在 `session_end` hook 或 `memory_forget` 中连接 |

---

## 八、后台任务（部分缺失）

### 8.1 列出任务

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/tasks` |
| **作用** | 按类型 / 状态 / 资源查询后台任务列表 |
| **参数** | `task_type`、`status`、`resource_id`、`limit` |
| **插件现状** | ❌ 未实现（仅对接了单任务查询 `GET /api/v1/tasks/{id}`） |

---

## 九、统计 / 记忆健康（完全缺失）

### 9.1 记忆统计

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/stats/memories` |
| **作用** | 查看记忆库整体健康指标，按 category 可选过滤 |
| **插件现状** | ❌ 未实现 |

### 9.2 会话提取统计

| 项 | 详情 |
|----|------|
| **端点** | `GET /api/v1/stats/sessions/{id}` |
| **作用** | 查看单会话的记忆提取统计 |
| **插件现状** | ❌ 未实现 |
| **建议** | 可用于 Agent 自我诊断记忆质量 |

---

## 十、系统 / 运维（部分缺失）

### 10.1 等待处理完成

| 项 | 详情 |
|----|------|
| **端点** | `POST /api/v1/system/wait` |
| **作用** | 阻塞等待所有队列任务完成 |
| **参数** | `timeout` |
| **插件现状** | ❌ 未实现 |

### 10.2 就绪检查

| 项 | 详情 |
|----|------|
| **端点** | `GET /ready` |
| **作用** | K8s 风格就绪探针（检查 AGFS、VectorDB、APIKeyManager） |
| **插件现状** | ❌ 未实现（仅用了 `/health`） |

### 10.3 Prometheus 指标

| 项 | 详情 |
|----|------|
| **端点** | `GET /metrics` |
| **作用** | Prometheus 格式的系统指标 |
| **插件现状** | ❌ 未实现 |

---

## 十一、观测与调试（完全缺失）

以下端点可用于运行时诊断，建议至少对接部分关键指标。

| 端点 | 作用 |
|------|------|
| `GET /api/v1/observer/queue` | 队列状态 |
| `GET /api/v1/observer/vikingdb` | 向量存储状态 |
| `GET /api/v1/observer/vlm` | VLM 使用情况 |
| `GET /api/v1/observer/lock` | 锁子系统 |
| `GET /api/v1/observer/retrieval` | 检索质量指标 |
| `GET /api/v1/observer/system` | 聚合系统视图 |
| `GET /api/v1/debug/vector/scroll` | 分页遍历向量 |
| `GET /api/v1/debug/vector/count` | 向量计数 |

---

## 十二、多租户管理（完全缺失）

以下为管理员级别 API，需 `root_api_key` 权限。

| 端点 | 作用 |
|------|------|
| `POST /api/v1/admin/accounts` | 创建租户账户 |
| `GET /api/v1/admin/accounts` | 列出账户 |
| `DELETE /api/v1/admin/accounts/{id}` | 删除账户（级联清除） |
| `POST /api/v1/admin/accounts/{id}/users` | 创建用户 |
| `GET /api/v1/admin/accounts/{id}/users` | 列出用户 |
| `DELETE /api/v1/admin/accounts/{id}/users/{uid}` | 删除用户 |
| `PUT /api/v1/admin/accounts/{id}/users/{uid}/role` | 修改角色 |
| `POST /api/v1/admin/accounts/{id}/users/{uid}/key` | 重新生成 API Key |

---

## 十三、Bot 代理（完全缺失）

需 `--with-bot` 启动参数，用于对接 Vikingbot 聊天服务。

| 端点 | 作用 |
|------|------|
| `GET /bot/v1/health` | Bot 健康检查 |
| `POST /bot/v1/chat` | 聊天（JSON） |
| `POST /bot/v1/chat/stream` | 流式聊天（SSE） |

---

## 优先级建议

### P0 — 核心能力补全（建议立即实现）

| # | 能力 | 理由 |
|---|------|------|
| 1 | **Add Resource**（含 temp_upload） | 知识库导入是最核心的用户需求，当前完全无法从插件端添加文档/URL |
| 2 | **Content Abstract / Overview** | 多层级摘要可大幅减少 recall 时的 token 消耗 |
| 3 | **Content Write** | 允许 Agent 直接写入/更新知识节点，不局限于 session commit |
| 4 | **Session Used 反馈** | 闭环检索→使用→反馈循环，持续提升检索准确性 |

### P1 — 高价值补充（建议短期实现）

| # | 能力 | 理由 |
|---|------|------|
| 5 | **Session-aware Search** | 替代 `find` 使用 `search`，利用会话上下文提升排序 |
| 6 | **Relations 图谱操作** | 知识关联能力，构建更结构化的知识网络 |
| 7 | **FS Tree / Stat** | Agent 可全局浏览知识库结构，辅助决策 |
| 8 | **Grep / Glob 搜索** | 精确文本 + 模式匹配搜索，补充语义检索的不足 |
| 9 | **Add Skill** | 注入结构化指令/技能数据 |

### P2 — 运维与管理（建议中期实现）

| # | 能力 | 理由 |
|---|------|------|
| 10 | **Pack Import / Export** | 知识库迁移与备份 |
| 11 | **Content Reindex** | 知识库维护，向量重建 |
| 12 | **FS Mkdir / Mv** | 知识库组织管理 |
| 13 | **Session Create / List / Delete** | 完整会话生命周期管理 |
| 14 | **Memory Stats** | 记忆健康监控 |

### P3 — 高级功能（建议按需实现）

| # | 能力 | 理由 |
|---|------|------|
| 15 | **Observer / Debug** | 运行时诊断，排查问题 |
| 16 | **System Wait / Ready** | 部署运维场景 |
| 17 | **Multi-tenant Admin** | 多租户管理（按需） |
| 18 | **Bot Proxy** | 对接 Vikingbot 聊天（按需） |
| 19 | **Task List** | 批量任务监控 |

---

## 插件内部待修复项

除了新能力对接外，当前插件还有以下内部问题需关注：

| 问题 | 说明 |
|------|------|
| `deleteSession()` 未被调用 | Client 中已实现但无使用点，建议在 `session_end` hook 中连接 |
| `getCaptureDecision()` 死代码 | `context-engine.ts` import 了但未使用，`captureMode` / `captureMaxLength` 配置项未生效 |
| `after_compaction` hook 为空 | 仅注册了占位，未实现逻辑 |
| `ingest` / `ingestBatch` 始终返回未摄取 | Context engine 的摄取路径是 no-op，设计上是否应在此路径对接 `content/write`？ |
