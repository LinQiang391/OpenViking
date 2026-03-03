# OpenClaw + OpenViking Docker 使用手册

将 [OpenViking](https://github.com/openviking/OpenViking) 作为 [OpenClaw](https://github.com/openclaw/openclaw) 的长期记忆后端。以下为 **Docker 镜像使用手册**（打包 → 启动 → 环境变量 → 持久化）。

---

## 一、打包镜像

在 OpenViking 仓库根目录执行：

```bash
cd /path/to/OpenViking
docker build -f examples/openclaw-memory-plugin/docker/Dockerfile -t openclaw-openviking:latest .
```

无 Docker 权限时可用：`sg docker -c "docker build -f examples/openclaw-memory-plugin/docker/Dockerfile -t openclaw-openviking:latest ."`

---

## 二、启动容器

### 2.1 最小化启动（OpenClaw + OpenViking，不挂载）

仅传必要环境变量即可运行，数据写在容器内，重启容器保留，删容器即丢。

```bash
docker run -d --name oc-ov -p 18789:18789 -p 1933:1933 \
  -e OPENVIKING_ARK_API_KEY="<火山 Ark API Key>" \
  -e OPENCLAW_DEFAULT_MODEL="zai/glm-4.7" \
  -e ZAI_API_KEY="<智谱 API Key>" \
  openclaw-openviking:latest
```

- 网关：`http://<主机>:18789`，记忆服务端口 `1933`。
- 若使用其他 LLM，替换 `OPENCLAW_DEFAULT_MODEL` 并传入对应 Key（见下方环境变量表）。

### 2.2 仅运行 OpenClaw（不启用 OpenViking 记忆）

无需火山 Key，只跑 OpenClaw 网关：

```bash
docker run -d --name oc-ov -p 18789:18789 \
  -e OPENVIKING_ENABLED=0 \
  -e OPENCLAW_DEFAULT_MODEL="zai/glm-4.7" \
  -e ZAI_API_KEY="<智谱 API Key>" \
  openclaw-openviking:latest
```

### 2.3 完全自主配置（环境变量一键启动）

下面为**完全支持自主配置的启动命令**：所有环境变量均以占位符 `your-xxx` 给出，不写具体默认值；**各变量含义、默认值、是否可选**见下方 **三、环境变量**（3.1 OpenViking、3.2 OpenClaw）表格。复制后按占位替换为实际值即可。

```bash
docker run -d --name oc-ov -p 18766:18789 -p 1966:1933 \
  -e OPENVIKING_ENABLED=your-enable-openviking \
  -e OPENVIKING_SERVER_HOST=your-server-host \
  -e OPENVIKING_SERVER_PORT=your-server-port \
  -e OPENVIKING_AGFS_PORT=your-agfs-port \
  -e OPENVIKING_WORKSPACE=your-workspace-path \
  -e OPENVIKING_TARGET_URI=your-target-uri \
  -e OPENVIKING_AUTO_RECALL=your-auto-recall \
  -e OPENVIKING_AUTO_CAPTURE=your-auto-capture \
  -e OPENVIKING_REGENERATE_CONFIG=your-regenerate-config \
  -e OPENVIKING_ARK_API_KEY=your-openviking-ark-api-key \
  -e OPENVIKING_VLM_PROVIDER=your-vlm-provider \
  -e OPENVIKING_VLM_API_BASE=your-vlm-api-base \
  -e OPENVIKING_VLM_API_KEY=your-vlm-api-key \
  -e OPENVIKING_VLM_MODEL=your-vlm-model \
  -e OPENVIKING_EMBEDDING_PROVIDER=your-embedding-provider \
  -e OPENVIKING_EMBEDDING_API_BASE=your-embedding-api-base \
  -e OPENVIKING_EMBEDDING_API_KEY=your-embedding-api-key \
  -e OPENVIKING_EMBEDDING_MODEL=your-embedding-model \
  -e OPENVIKING_EMBEDDING_DIMENSION=your-embedding-dimension \
  -e OPENVIKING_EMBEDDING_INPUT=your-embedding-input \
  -e OPENCLAW_GATEWAY_PORT=your-gateway-port \
  -e OPENCLAW_REAPPLY_CONFIG=your-reapply-config \
  -e OPENCLAW_DEFAULT_MODEL=your-default-model \
  -e VOLCANO_ENGINE_API_KEY=your-volcano-engine-api-key \
  -v openclaw-data:/root/.openclaw \
  -v openviking-data:/root/.openviking \
  openclaw-openviking:latest
```

- 复制后将上述 `your-xxx` 占位替换为真实值；未传的变量将使用 **3.1、3.2 表中的默认值**。
- 若用智谱/Claude/OpenAI 等对话：改 `OPENCLAW_DEFAULT_MODEL` 为对应模型 ID，并传入该 provider 的 Key 变量（如 `ZAI_API_KEY`、`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`），见 3.2 表。
- 环境变量完整说明（含默认值、类型、可选性）见 **三、环境变量**（3.1 OpenViking、3.2 OpenClaw）。

### 2.4 挂载目录启动（持久化）

见 [四、持久化](#四持久化)。

---

## 三、环境变量

entrypoint 透传以下环境变量；未传时使用默认值。**变量名、含义、默认值、类型、是否可选、说明**分 OpenViking 与 OpenClaw 两节列出，不遗漏。

---

### 3.1 OpenViking 环境变量

用于记忆服务与 ov.conf 生成；与 OpenClaw 对话用 Key 无关。

| 变量名 | 含义 | 默认值 | 类型 | 是否可选 | 说明 |
|--------|------|--------|------|----------|------|
| `OPENVIKING_ENABLED` | 是否启用 OpenViking 记忆插件 | `1` | string | 可选 | `1` 或 `true` 启用；`0` 或 `false` 仅跑 OpenClaw、不生成 ov.conf。若曾启用过再关闭，建议同时设 `OPENCLAW_REAPPLY_CONFIG=1` |
| `OPENVIKING_SERVER_HOST` | OpenViking 服务监听地址 | `127.0.0.1` | string | 可选 | 写入 ov.conf server.host |
| `OPENVIKING_SERVER_PORT` | OpenViking 服务监听端口 | `1933` | string | 可选 | 写入 ov.conf；映射宿主机时用 `-p <宿主机>:1933` |
| `OPENVIKING_AGFS_PORT` | AGFS 内部端口 | `1833` | string | 可选 | 写入 ov.conf storage.agfs.port |
| `OPENVIKING_WORKSPACE` | 工作目录（数据与存储） | `$OPENVIKING_HOME/data` | string | 可选 | 即 `/root/.openviking/data`；写入 ov.conf storage.workspace |
| `OPENVIKING_TARGET_URI` | 记忆目标 URI | `viking://user/memories` | string | 可选 | 写入 OpenClaw 插件配置 |
| `OPENVIKING_AUTO_RECALL` | 是否自动回忆 | `true` | string | 可选 | 写入插件配置，`true`/`false` |
| `OPENVIKING_AUTO_CAPTURE` | 是否自动捕获记忆 | `true` | string | 可选 | 写入插件配置，`true`/`false` |
| `OPENVIKING_REGENERATE_CONFIG` | 是否每次启动按环境变量重写 ov.conf | `0` | string | 可选 | `1` 时每次覆盖已有 ov.conf；`0` 时已有文件不碰，用户可完全自主编辑 |
| `OPENVIKING_ARK_API_KEY` | 火山 Ark API Key（VLM/Embedding 未单独设时共用） | （无） | string | 必填* | 启用 OpenViking 且由环境变量生成 ov.conf 时必填；或分别填下面 VLM/Embedding 的 Key。与 OpenClaw 对话 Key 无关 |
| `OPENVIKING_VLM_PROVIDER` | 记忆用 VLM 的厂商（provider） | `volcengine` | string | 可选 | 如 `volcengine`、`openai`；写入 ov.conf vlm.provider |
| `OPENVIKING_VLM_API_BASE` | 记忆用 VLM 的 API 根地址 | `https://ark.cn-beijing.volces.com/api/v3` | string | 可选 | VLM 调用的 API 根 URL（如火山 Ark、OpenAI 等），用于记忆中的视觉/多模态理解；与 Embedding 可不同。未设时用 `OPENVIKING_API_BASE` |
| `OPENVIKING_VLM_API_KEY` | 记忆用 VLM 的 API Key | 同 `OPENVIKING_ARK_API_KEY` | string | 必填* | 未设时沿用 `OPENVIKING_ARK_API_KEY`；可与 Embedding 不同厂商 |
| `OPENVIKING_VLM_MODEL` | 记忆用 VLM 的模型名 | `doubao-seed-1-8-251228` | string | 可选 | 写入 ov.conf vlm.model |
| `OPENVIKING_EMBEDDING_PROVIDER` | 记忆用 Embedding 的厂商 | `volcengine` | string | 可选 | 如 `volcengine`、`openai`、`jina`；写入 ov.conf embedding.dense.provider |
| `OPENVIKING_EMBEDDING_API_BASE` | 记忆用 Embedding 的 API 根地址 | `https://ark.cn-beijing.volces.com/api/v3` | string | 可选 | Embedding 调用的 API 根 URL；与 VLM 可不同。未设时用 `OPENVIKING_API_BASE` |
| `OPENVIKING_EMBEDDING_API_KEY` | 记忆用 Embedding 的 API Key | 同 `OPENVIKING_ARK_API_KEY` | string | 必填* | 未设时沿用 `OPENVIKING_ARK_API_KEY` |
| `OPENVIKING_EMBEDDING_MODEL` | 记忆用 Embedding 的模型名 | `doubao-embedding-vision-250615` | string | 可选 | 写入 ov.conf embedding.dense.model |
| `OPENVIKING_EMBEDDING_DIMENSION` | Embedding 向量维度 | `1024` | string | 可选 | 如 OpenAI 常用 3072；写入 ov.conf |
| `OPENVIKING_EMBEDDING_INPUT` | Embedding 输入类型 | `multimodal` | string | 可选 | `multimodal` 或 `text`；写入 ov.conf |
| `OPENVIKING_API_BASE` | VLM/Embedding 未单独设时的共用 API 根地址 | `https://ark.cn-beijing.volces.com/api/v3` | string | 可选 | 仅作 `OPENVIKING_VLM_API_BASE`、`OPENVIKING_EMBEDDING_API_BASE` 的默认值 |
| `OPENVIKING_HOME` | OpenViking 配置与数据根目录（容器内） | `/root/.openviking` | string | 可选 | 挂载时与 `-v` 目标一致 |
| `OPENVIKING_CONFIG_FILE` | ov.conf 完整路径 | `$OPENVIKING_HOME/ov.conf` | string | 可选 | 一般无需改 |

\* 必填：启用 OpenViking 且由环境变量生成 ov.conf 时，至少填 `OPENVIKING_ARK_API_KEY` 或同时填 `OPENVIKING_VLM_API_KEY` 与 `OPENVIKING_EMBEDDING_API_KEY`。

---

### 3.2 OpenClaw 环境变量

用于对话模型与网关；根据 `OPENCLAW_DEFAULT_MODEL` 的 provider 填**对应一个** API Key 即可。

| 变量名 | 含义 | 默认值 | 类型 | 是否可选 | 说明 |
|--------|------|--------|------|----------|------|
| `OPENCLAW_DEFAULT_MODEL` | 默认对话模型 ID（provider/model） | （无） | string | 必填 | 如 `zai/glm-4.7`、`volcengine/doubao-seed-1-8-251228`、`anthropic/claude-sonnet-4`；详见 [模型说明](../docs/openclaw-models-zh.md) |
| `ZAI_API_KEY` | 智谱 GLM API Key | （无） | string | 按需 | 默认模型为 `zai/*` 时必填 |
| `ZHIPU_API_KEY` | 智谱 Key（旧名） | （无） | string | 按需 | 未设 `ZAI_API_KEY` 时作为 ZAI 使用 |
| `VOLCANO_ENGINE_API_KEY` | 火山引擎 Ark API Key（对话用） | （无） | string | 按需 | 默认模型为 `volcengine/*` 时必填；可与 OpenViking 共用同一 Key |
| `ANTHROPIC_API_KEY` | Claude (Anthropic) API Key | （无） | string | 按需 | 默认模型为 `anthropic/*` 时填 |
| `OPENAI_API_KEY` | OpenAI API Key | （无） | string | 按需 | 默认模型为 `openai/*` 时填 |
| `OPENROUTER_API_KEY` | OpenRouter API Key | （无） | string | 按需 | 默认模型为 `openrouter/*` 时填 |
| `MOONSHOT_API_KEY` | 月之暗面 / Kimi API Key | （无） | string | 按需 | 默认模型为 `moonshot/*` 时填 |
| `GOOGLE_API_KEY` | Google Gemini API Key | （无） | string | 按需 | 默认模型为 `google/*` 时填 |
| `GROQ_API_KEY` | Groq API Key | （无） | string | 按需 | 默认模型为 `groq/*` 时填 |
| 其他 provider Key | 其他 OpenClaw 支持的 provider | （无） | string | 按需 | 根据 [模型说明](../docs/openclaw-models-zh.md) 按需传入；**只填当前默认模型对应的一项** |
| `OPENCLAW_GATEWAY_PORT` | 网关监听端口（容器内） | `18789` | string | 可选 | 映射宿主机时用 `-p <宿主机端口>:18789` |
| `OPENCLAW_REAPPLY_CONFIG` | 是否本次启动强制重写 OpenClaw 配置 | `0` | string | 可选 | `1` 时覆盖已有 openclaw.json（引导配置重新执行） |
| `OPENCLAW_STATE_DIR` | OpenClaw 状态目录（容器内，配置与扩展所在） | `/root/.openclaw` | string | 可选 | 挂载时与 `-v` 目标一致；镜像用此变量直接指定状态目录，避免用 `OPENCLAW_HOME` 时产生 `.openclaw/.openclaw` 嵌套 |
| `OPENCLAW_HOME` | OpenClaw 的“用户 home”（OpenClaw 内部解析 `~/.openclaw` 为 `$OPENCLAW_HOME/.openclaw`） | （未设） | string | 可选 | 未设 `OPENCLAW_STATE_DIR` 时若设此项会导致状态目录变为 `$OPENCLAW_HOME/.openclaw`，出现一层嵌套；建议挂载时只改 `OPENCLAW_STATE_DIR` |

---

### 3.3 使用方式

- **最小化启动（全用环境变量）**：记忆用火山 Ark → 填 `OPENVIKING_ARK_API_KEY`（或分别填 `OPENVIKING_VLM_API_KEY`、`OPENVIKING_EMBEDDING_API_KEY`）；对话用智谱/Claude 等 → 填 `OPENCLAW_DEFAULT_MODEL` 及对应 Key。
- **OpenClaw 对话用火山 VLM（与 OpenViking 同源）**：和智谱用法一致，用 `OPENCLAW_DEFAULT_MODEL` 指定模型、用对应 Key 变量传 API Key 即可：
  - 智谱：`OPENCLAW_DEFAULT_MODEL=zai/glm-4.7` + `ZAI_API_KEY="xxx"`
  - 火山：`OPENCLAW_DEFAULT_MODEL=volcengine/doubao-seed-1-8-251228` + `VOLCANO_ENGINE_API_KEY="xxx"`（可与 `OPENVIKING_ARK_API_KEY` 同一把 Key）。其他火山模型：`volcengine/glm-4-7-251222`、`volcengine/kimi-k2-5-260127`（见 [模型说明](../docs/openclaw-models-zh.md)）。
- **VLM 与 Embedding 不同厂商**：直接设环境变量即可，例如 VLM 用 OpenAI、Embedding 用 Jina：
  `OPENVIKING_VLM_PROVIDER=openai` `OPENVIKING_VLM_API_BASE=https://api.openai.com/v1` `OPENVIKING_VLM_API_KEY=...` `OPENVIKING_VLM_MODEL=gpt-4-vision-preview`  
  `OPENVIKING_EMBEDDING_PROVIDER=jina` `OPENVIKING_EMBEDDING_API_BASE=https://api.jina.ai/v1` `OPENVIKING_EMBEDDING_API_KEY=...` `OPENVIKING_EMBEDDING_MODEL=jina-embeddings-v5-text-small` `OPENVIKING_EMBEDDING_DIMENSION=1024`
- **完全自管 ov.conf**：挂载目录后自己创建或编辑 `ov.conf`（挂载到 `/root/.openviking` 时，即卷根目录下的 `ov.conf`），不设 `OPENVIKING_REGENERATE_CONFIG=1` 时**不会覆盖**，用户可完全自主编辑该文件。
- **换对话模型**：改 `OPENCLAW_DEFAULT_MODEL` 及对应 Key；若需重写 OpenClaw 配置可加 `-e OPENCLAW_REAPPLY_CONFIG=1`。
- **关闭 OpenViking**：设 `OPENVIKING_ENABLED=0`；若此前已启用过，建议同时设一次 `OPENCLAW_REAPPLY_CONFIG=1`。

### 3.4 自管配置文件 ov.conf

若你更习惯直接改配置文件：在挂载到 `/root/.openviking` 的目录下维护 `ov.conf`（即卷根或宿主机目录下的 `ov.conf`），**不要**设 `OPENVIKING_REGENERATE_CONFIG=1`，则 entrypoint 不会覆盖该文件，你可完全自主编辑。支持的 provider、字段与示例见 OpenViking 官方文档：[配置说明](https://github.com/openviking/OpenViking/blob/main/docs/zh/guides/01-configuration.md)。

---

## 四、持久化

### 4.1 方式一：命名 volume（Docker 管理）

数据在 Docker 的 volume 目录，不在当前项目目录下：

```bash
docker run -d --name oc-ov -p 18789:18789 -p 1933:1933 \
  -e OPENVIKING_ARK_API_KEY="<火山 Key>" \
  -e OPENCLAW_DEFAULT_MODEL="zai/glm-4.7" \
  -e ZAI_API_KEY="<智谱 Key>" \
  -v openclaw-data:/root/.openclaw \
  -v openviking-data:/root/.openviking \
  openclaw-openviking:latest
```

查看 volume 实际路径：`docker volume inspect openclaw-data`（需 root 或 docker 组）。

### 4.2 方式二：挂载宿主机目录（绑定挂载）

数据落在你指定的目录，便于备份与直接改配置：

```bash
mkdir -p /data/openclaw-data /data/openviking-data

docker run -d --name oc-ov -p 18789:18789 -p 1933:1933 \
  -e OPENVIKING_ARK_API_KEY="<火山 Key>" \
  -e OPENCLAW_DEFAULT_MODEL="zai/glm-4.7" \
  -e ZAI_API_KEY="<智谱 Key>" \
  -v /data/openclaw-data:/root/.openclaw \
  -v /data/openviking-data:/root/.openviking \
  openclaw-openviking:latest
```

- 挂载后，**已有** `openclaw.json` / `ov.conf` 不会被 entrypoint 覆盖；**重启容器**不会清空这些文件。
- 若挂载的 OpenClaw 目录为空或没有 memory-openviking 插件，entrypoint 会自动从镜像内拷贝插件到挂载目录，避免 `plugin not found: memory-openviking`。

### 4.3 持久化时的配置行为

| 情况 | 行为 |
|------|------|
| 首次启动、挂载空目录 | 生成 `ov.conf`、OpenClaw 配置与插件；之后沿用 |
| 再次启动、目录已有配置 | 不覆盖；仅当设 `OPENVIKING_REGENERATE_CONFIG=1` 时重写 `ov.conf`，设 `OPENCLAW_REAPPLY_CONFIG=1` 时重写 OpenClaw 配置 |
| 修改配置后 | 建议先 `docker stop oc-ov`，改完宿主机上或 volume 内文件后再 `docker start oc-ov`，避免运行中写入导致进程退出 |

OpenClaw 配置实际路径（容器内）：`/root/.openclaw/openclaw.json`；挂载时在宿主机对应目录下找 `openclaw.json`（与卷根同级）。镜像通过 `OPENCLAW_STATE_DIR` 指定状态目录，避免用 `OPENCLAW_HOME` 时出现 `.openclaw/.openclaw` 的嵌套。

### 4.4 通过改配置关闭 OpenViking（不重建容器）

在持久化目录中编辑 `openclaw.json`（路径见上），将 `plugins.allow` 改为 `[]`，`plugins.load.paths` 改为 `[]`，删除或置空 `plugins.slots.memory`，保存后执行 `docker restart oc-ov`。若要再次启用，改回原样并重启即可。
