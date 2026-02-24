# Server Deployment

OpenViking can run as a standalone HTTP server, allowing multiple clients to connect over the network.

## Quick Start

```bash
# Start server (reads ~/.openviking/ov.conf by default)
python -m openviking serve

# Or specify a custom config path
python -m openviking serve --config /path/to/ov.conf

# Verify it's running
curl http://localhost:1933/health
# {"status": "ok"}
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config` | Path to ov.conf file | `~/.openviking/ov.conf` |
| `--host` | Host to bind to | `0.0.0.0` |
| `--port` | Port to bind to | `1933` |

**Examples**

```bash
# With default config
python -m openviking serve

# With custom port
python -m openviking serve --port 8000

# With custom config, host, and port
python -m openviking serve --config /path/to/ov.conf --host 127.0.0.1 --port 8000
```

## Configuration

The server reads all configuration from `ov.conf`. See [Configuration Guide](./01-configuration.md) for full details on config file format.

The `server` section in `ov.conf` controls server behavior:

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

## Deployment Modes

### Standalone (Embedded Storage)

Server manages local AGFS and VectorDB. Configure the storage path in `ov.conf`:

```json
{
  "storage": {
    "agfs": { "backend": "local", "path": "/data/openviking" },
    "vectordb": { "backend": "local", "path": "/data/openviking" }
  }
}
```

```bash
python -m openviking serve
```

### Hybrid (Remote Storage)

Server connects to remote AGFS and VectorDB services. Configure remote URLs in `ov.conf`:

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

### Cloud (Volcengine)

Use Volcengine TOS (object storage) + VikingDB (vector database) as backends for production deployments.

See `examples/cloud/ov.conf.example` for a complete config template. Key configuration:

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

**Steps**:

1. Copy the config template and fill in your credentials:
   ```bash
   cp examples/cloud/ov.conf.example ~/.openviking/ov.conf
   # Edit ov.conf, replace all <PLACEHOLDER> values
   ```

2. Start the server:
   ```bash
   python -m openviking serve
   ```

3. Verify all dependencies are connected:
   ```bash
   curl http://localhost:1933/ready
   # {"status": "ok", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok"}}
   ```

### Kubernetes

The project includes a Helm chart at `examples/k8s-helm/`.

```bash
# Install
helm install openviking examples/k8s-helm/ \
  --set-file config=~/.openviking/ov.conf

# Verify pod readiness
kubectl get pods -l app.kubernetes.io/name=openviking
```

The Helm chart configures two probes:

| Probe | Path | Purpose |
|-------|------|---------|
| `livenessProbe` | `/health` | Process liveness check; failure triggers pod restart |
| `readinessProbe` | `/ready` | Dependency readiness check; failure removes pod from traffic |

## Health Checks

OpenViking provides two health check endpoints, both unauthenticated:

**`GET /health`** — Process liveness check. Always returns `{"status": "ok"}`. Used for K8s livenessProbe and basic connectivity verification.

**`GET /ready`** — Dependency readiness check. Verifies connectivity to AGFS, VectorDB, and APIKeyManager. Returns 200 when all components are healthy, 503 otherwise.

```bash
# Basic liveness check
curl http://localhost:1933/health

# Full readiness check
curl http://localhost:1933/ready
# OK: {"status": "ok", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok"}}
# Degraded: {"status": "degraded", "checks": {"agfs": "ok", "vectordb": "error: ...", "api_key_manager": "ok"}}
```

## Connecting Clients

### Python SDK

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key", agent_id="my-agent")
client.initialize()

results = client.find("how to use openviking")
client.close()
```

### CLI

The CLI reads connection settings from `ovcli.conf`. Create `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

Or set the config path via environment variable:

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

Then use the CLI:

```bash
python -m openviking ls viking://resources/
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## Related Documentation

- [Authentication](04-authentication.md) - API key setup
- [Monitoring](05-monitoring.md) - Health checks and observability
- [API Overview](../api/01-overview.md) - Complete API reference
