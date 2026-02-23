# OpenViking Installation Guide

Complete installation guide for OpenViking - the Context Database for AI Agents.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation Options](#installation-options)
  - [Option 1: Python Package (Recommended)](#option-1-python-package-recommended)
  - [Option 2: Rust CLI (ov)](#option-2-rust-cli-ov)
- [Configuration](#configuration)
  - [Server Configuration (ov.conf)](#server-configuration-ovconf)
  - [CLI Configuration (ovcli.conf)](#cli-configuration-ovcliconf)
- [Verification](#verification)
  - [Verify Python Package](#verify-python-package)
  - [Verify Server](#verify-server)
  - [Verify CLI](#verify-cli)
- [Example Skills](#example-skills)
- [Troubleshooting](#troubleshooting)

---

## Overview

OpenViking consists of two main components:

1. **OpenViking Server** - The core context database (Python package)
2. **ov CLI** - Command-line interface for interacting with the server (Rust binary)

You can use OpenViking in two modes:
- **Embedded Mode**: Direct Python API calls from your application
- **Server Mode**: HTTP server with client connections (Python SDK, CLI, or HTTP)

---

## Prerequisites

Before installing OpenViking, ensure you have:

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | Required for server package |
| Operating System | Linux, macOS, Windows | All supported |
| Network | Stable connection | For model APIs and dependencies |
| API Keys | VLM + Embedding | From your model provider(s) |

**Supported Model Providers:**
- Volcengine (Doubao)
- OpenAI
- Anthropic (Claude)
- DeepSeek
- Google (Gemini)
- Moonshot (Kimi)
- Zhipu (GLM)
- DashScope (Qwen)
- MiniMax
- OpenRouter
- vLLM (local models)

---

## Installation Options

### Option 1: Python Package (Recommended)

Install the OpenViking server and Python SDK:

```bash
pip install openviking
```

Or with `uv` (faster):

```bash
uv pip install openviking
```

**Verify installation:**

```bash
python -c "import openviking; print(openviking.__version__)"
```

Expected output: version number (e.g., `0.2.0`)

---

### Option 2: Rust CLI (ov)

The `ov` CLI provides a fast, convenient interface to interact with OpenViking server.

#### Quick Install (Linux/macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

#### Install from Source

Requires Rust toolchain:

```bash
# Clone the repository
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking

# Install with cargo
cargo install --path crates/ov_cli

# Or install directly from git
cargo install --git https://github.com/volcengine/OpenViking ov_cli
```

**Verify installation:**

```bash
ov --version
```

Expected output: version number (e.g., `ov 0.2.0`)

---

## Configuration

### Server Configuration (ov.conf)

Create the server configuration file at `~/.openviking/ov.conf`:

```bash
mkdir -p ~/.openviking
```

**Minimal configuration:**

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "api_key": "your-api-key",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-1-8-251228",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

**Full configuration template** (see `examples/ov.conf.example`):

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": null,
    "cors_origins": ["*"]
  },
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "local",
      "path": "./data"
    },
    "agfs": {
      "port": 1833,
      "path": "./data",
      "backend": "local"
    }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "api_key": "your-api-key",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-1-8-251228",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.0,
    "max_retries": 2
  },
  "auto_generate_l0": true,
  "auto_generate_l1": true,
  "default_search_mode": "thinking",
  "default_search_limit": 3,
  "log_level": "INFO"
}
```

**Set environment variable:**

```bash
# Linux/macOS
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf

# Windows (PowerShell)
$env:OPENVIKING_CONFIG_FILE = "$HOME/.openviking/ov.conf"

# Windows (CMD)
set "OPENVIKING_CONFIG_FILE=%USERPROFILE%\.openviking\ov.conf"
```

---

### CLI Configuration (ovcli.conf)

Create the CLI configuration file at `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-api-key"
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `url` | OpenViking server URL | `http://localhost:1933` |
| `api_key` | API key for authentication | `null` (if server has no auth) |

**Alternative config location:**

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

---

## Verification

### Verify Python Package

```bash
python -c "
import openviking as ov
print(f'OpenViking version: {ov.__version__}')
print('Python package installed successfully!')
"
```

### Verify Server

1. **Start the server:**

```bash
# Using the installed command
openviking-server

# Or using Python module
python -m openviking serve

# With custom config
python -m openviking serve --config /path/to/ov.conf --port 1933
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:1933
```

2. **Check health endpoint:**

```bash
curl http://localhost:1933/health
```

Expected output:
```json
{"status": "ok"}
```

3. **Verify with Python SDK:**

```python
import openviking as ov

# Test connection
client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()
print("Server connection successful!")
client.close()
```

### Verify CLI

```bash
# Check version
ov --version

# Check system status (requires server running)
ov system health

# Test basic commands
ov ls
ov config show
```

**Full integration test:**

```bash
# Add a test resource
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md --wait

# List resources
ov ls viking://resources

# Search
ov find "what is openviking"
```

---

## Example Skills

OpenViking includes example skills demonstrating common patterns. These are located in `examples/skills/`:

| Skill | Description | Trigger |
|-------|-------------|---------|
| `adding-memory` | Store memories and learnings | `ovm` keyword |
| `adding-resource` | Import files, URLs, directories | `ovr` keyword |
| `searching-context` | Search memories and resources | `ovs` keyword |

**Using example skills:**

1. Review the skill documentation:
   - `examples/skills/adding-memory/SKILL.md`
   - `examples/skills/adding-resource/SKILL.md`
   - `examples/skills/searching-context/SKILL.md`

2. Test the skills with CLI:

```bash
# Adding memory
ov add-memory "User prefers Python for data processing tasks"

# Adding resource
ov add-resource https://example.com/docs --wait

# Searching context
ov search "Python data processing"
```

3. See `examples/skills/tests.md` for comprehensive test cases.

---

## Troubleshooting

### Common Issues

#### 1. Import Error: No module named 'openviking'

**Cause:** Package not installed or wrong Python environment.

**Solution:**
```bash
pip install openviking
# or
uv pip install openviking
```

#### 2. Connection refused when connecting to server

**Cause:** Server not running or wrong URL.

**Solution:**
```bash
# Check if server is running
ps aux | grep openviking-server

# Start server
openviking-server

# Verify URL in ovcli.conf matches server host/port
```

#### 3. API key errors

**Cause:** Invalid or missing API key in configuration.

**Solution:**
- Check `api_key` in `~/.openviking/ov.conf`
- Verify key is valid with your model provider
- Check `api_base` URL is correct

#### 4. Embedding model errors

**Cause:** Wrong dimension or model name.

**Solution:**
- Verify `dimension` matches your embedding model
- Common dimensions: 1024 (Doubao), 3072 (OpenAI text-embedding-3-large)
- Check `input: "multimodal"` for vision embedding models

#### 5. CLI command not found

**Cause:** `ov` not in PATH.

**Solution:**
```bash
# Check if installed
cargo install --list | grep ov_cli

# Add cargo bin to PATH
export PATH="$HOME/.cargo/bin:$PATH"

# Or reinstall with full path
cargo install --path crates/ov_cli --force
```

### Getting Help

- **Documentation:** https://www.openviking.ai/docs
- **GitHub Issues:** https://github.com/volcengine/OpenViking/issues
- **Discord:** https://discord.com/invite/eHvx8E9XF3

---

## Next Steps

After successful installation:

1. **Read the Quick Start:** See `README.md` or [docs](https://www.openviking.ai/docs)
2. **Try Examples:** Run `examples/quick_start.py`
3. **Explore Skills:** Review `examples/skills/`
4. **Server Deployment:** See `docs/en/getting-started/03-quickstart-server.md`

---

## Summary Checklist

- [ ] Python 3.10+ installed
- [ ] `pip install openviking` completed successfully
- [ ] `ov` CLI installed (optional but recommended)
- [ ] `~/.openviking/ov.conf` created with model credentials
- [ ] `~/.openviking/ovcli.conf` created (for CLI usage)
- [ ] `OPENVIKING_CONFIG_FILE` environment variable set
- [ ] Server starts without errors
- [ ] Health endpoint returns `{"status": "ok"}`
- [ ] CLI can connect and list resources
- [ ] Example skills documentation reviewed
