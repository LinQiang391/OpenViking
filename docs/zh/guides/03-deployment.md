# 服务端部署

OpenViking 可以作为独立的 HTTP 服务器运行，允许多个客户端通过网络连接。

## 快速开始

```bash
# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
python -m openviking serve

# 配置文件在其他位置时，通过 --config 指定
python -m openviking serve --config /path/to/ov.conf

# 验证服务器是否运行
curl http://localhost:1933/health
# {"status": "ok"}
```

## 命令行选项

| 选项 | 描述 | 默认值 |
|------|------|--------|
| `--config` | 配置文件路径 | `~/.openviking/ov.conf` |
| `--host` | 绑定的主机地址 | `0.0.0.0` |
| `--port` | 绑定的端口 | `1933` |

**示例**

```bash
# 使用默认配置
python -m openviking serve

# 使用自定义端口
python -m openviking serve --port 8000

# 指定配置文件、主机地址和端口
python -m openviking serve --config /path/to/ov.conf --host 127.0.0.1 --port 8000
```

## 配置

服务端从 `ov.conf` 读取所有配置。配置文件各段详情见 [配置指南](01-configuration.md)。

`ov.conf` 中的 `server` 段控制服务端行为：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "your-secret-root-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "agfs": { "backend": "local", "path": "/data/openviking" },
    "vectordb": { "backend": "local", "path": "/data/openviking" }
  }
}
```

## 部署模式

### 独立模式（嵌入存储）

服务器管理本地 AGFS 和 VectorDB。在 `ov.conf` 中配置本地存储路径：

```json
{
  "storage": {
    "agfs": { "backend": "local", "path": "./data" },
    "vectordb": { "backend": "local", "path": "./data" }
  }
}
```

```bash
python -m openviking serve
```

### 混合模式（远程存储）

服务器连接到远程 AGFS 和 VectorDB 服务。在 `ov.conf` 中配置远程地址：

```json
{
  "storage": {
    "agfs": { "backend": "remote", "url": "http://agfs:1833" },
    "vectordb": { "backend": "remote", "url": "http://vectordb:8000" }
  }
}
```

```bash
python -m openviking serve
```

### 云上模式（火山引擎）

使用火山引擎 TOS（对象存储）+ VikingDB（向量数据库）作为后端，适合生产环境部署。

完整配置模板见 `examples/cloud/ov.conf.example`，核心配置如下：

```json
{
  "storage": {
    "agfs": {
      "backend": "s3",
      "port": 1833,
      "s3": {
        "bucket": "<TOS_BUCKET_NAME>",
        "region": "cn-beijing",
        "access_key": "<TOS_ACCESS_KEY>",
        "secret_key": "<TOS_SECRET_KEY>",
        "endpoint": "https://tos-cn-beijing.ivolces.com",
        "prefix": "openviking",
        "use_ssl": true,
        "use_path_style": false
      }
    },
    "vectordb": {
      "backend": "volcengine",
      "name": "context",
      "project": "openviking",
      "dimension": 1024,
      "volcengine": {
        "ak": "<VIKINGDB_ACCESS_KEY>",
        "sk": "<VIKINGDB_SECRET_KEY>",
        "region": "cn-beijing"
      }
    }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "api_key": "<ARK_API_KEY>",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-1-8-251228",
    "api_key": "<ARK_API_KEY>",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "root_api_key": "<ROOT_API_KEY>",
    "cors_origins": ["*"]
  }
}
```

**步骤**：

1. 复制配置模板并填写实际凭证：
   ```bash
   cp examples/cloud/ov.conf.example ~/.openviking/ov.conf
   # 编辑 ov.conf，替换所有 <PLACEHOLDER> 为实际值
   ```

2. 启动服务：
   ```bash
   python -m openviking serve
   ```

3. 验证所有依赖连通：
   ```bash
   curl http://localhost:1933/ready
   # {"status": "ok", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok"}}
   ```

### Kubernetes 部署

项目提供 Helm Chart，见 `examples/k8s-helm/`。

```bash
# 安装
helm install openviking examples/k8s-helm/ \
  --set-file config=~/.openviking/ov.conf

# 验证 Pod 就绪
kubectl get pods -l app.kubernetes.io/name=openviking
```

Helm Chart 中配置了两个探针：

| 探针 | 路径 | 用途 |
|------|------|------|
| `livenessProbe` | `/health` | 进程存活检查，失败则重启 Pod |
| `readinessProbe` | `/ready` | 依赖就绪检查，失败则摘除流量 |

## 健康检查

OpenViking 提供两个健康检查端点，均无需认证：

**`GET /health`** — 进程存活检查，始终返回 `{"status": "ok"}`。用于 K8s livenessProbe 和基本连通性验证。

**`GET /ready`** — 依赖就绪检查，验证 AGFS、VectorDB、APIKeyManager 的连通性。全部正常返回 200，有组件异常返回 503。

```bash
# 基本存活检查
curl http://localhost:1933/health

# 完整就绪检查
curl http://localhost:1933/ready
# 成功: {"status": "ok", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok"}}
# 异常: {"status": "degraded", "checks": {"agfs": "ok", "vectordb": "error: ...", "api_key_manager": "ok"}}
```

## 连接客户端

### Python SDK

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key", agent_id="my-agent")
client.initialize()

results = client.find("how to use openviking")
client.close()
```

### CLI

CLI 从 `ovcli.conf` 读取连接配置。在 `~/.openviking/ovcli.conf` 中配置：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

也可通过 `OPENVIKING_CLI_CONFIG_FILE` 环境变量指定配置文件路径：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## 相关文档

- [认证](04-authentication.md) - API Key 设置
- [监控](05-monitoring.md) - 健康检查与可观测性
- [API 概览](../api/01-overview.md) - 完整 API 参考
