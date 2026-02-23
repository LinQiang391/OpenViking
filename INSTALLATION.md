# OpenViking Installation Guide

Complete installation guide for OpenViking - the Context Database for AI Agents.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Step 1: Install uv](#step-1-install-uv)
- [Step 2: Install OpenViking Server](#step-2-install-openviking-server)
- [Step 3: Install ov CLI (Required)](#step-3-install-ov-cli-required)
- [Step 4: Install Skills](#step-4-install-skills)
- [Configuration](#configuration)
  - [Server Configuration (ov.conf)](#server-configuration-ovconf)
  - [CLI Configuration (ovcli.conf)](#cli-configuration-ovcliconf)
- [Running the Server](#running-the-server)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

---

## Overview

OpenViking consists of three components:

1. **OpenViking Server** - The core context database (Python package)
2. **ov CLI** - Command-line interface for fast skill execution (Rust binary) **REQUIRED**
3. **Skills** - Agent capabilities for memory, resources, and search

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenViking Server                        │
│                   (Runs anywhere: local,                    │
│              cloud, VM, container, etc.)                    │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Skills     │  │   Memory     │  │   Resources  │      │
│  │  (installed) │  │  (context)   │  │  (files/URLs)│      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
         ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
         │  ov CLI │   │  ov CLI │   │  ov CLI │
         │  (PC)   │   │ (Laptop)│   │ (Mobile)│
         └─────────┘   └─────────┘   └─────────┘
```

**Key Benefits:**
- **Server can run anywhere** - local machine, cloud VM, container, or dedicated server
- **Multiple clients can share one server** - PC, laptop, mobile devices all connect to the same OpenViking instance
- **ov CLI is required** - enables fast skill execution and is the primary interface for agent operations
- **Server mode is required for skills** - provides better performance and enables skill functionality

---

## Prerequisites

Before installing OpenViking, ensure you have:

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | Required for server |
| Rust | Latest | Required for building ov CLI |
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

## Step 1: Install uv

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and resolver. **uv is the recommended and only supported way to install OpenViking** - it automatically manages virtual environments, ensuring clean, isolated installations without polluting your system Python.

### Install uv

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Verify installation:**
```bash
uv --version
```

Expected output: version number (e.g., `uv 0.5.x`)

---

## Step 2: Install OpenViking Server

Install the OpenViking server and Python SDK using uv. **uv automatically creates and manages a virtual environment** - no manual `venv` or `source venv/bin/activate` needed.

```bash
uv pip install openviking
```

**Verify installation:**

```bash
uv run python -c "import openviking; print(openviking.__version__)"
```

Expected output: version number (e.g., `0.1.18`)

---

## Step 3: Install ov CLI (Required)

The `ov` CLI is **required** for OpenViking. It provides:
- Fast skill execution
- Primary interface for agent operations
- Efficient communication with the server

### Quick Install (Linux/macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

### Install from Source

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

Expected output: version number (e.g., `0.1.0`)

---

## Step 4: Install Skills

Skills are agent capabilities that enable OpenViking's core functionality. Three skills are available:

| Skill | Description | Trigger Keyword | Files |
|-------|-------------|-----------------|-------|
| **adding-memory** | Store memories and learnings from conversations | `ovm` | `examples/skills/adding-memory/SKILL.md` |
| **adding-resource** | Import files, URLs, or directories into context | `ovr` | `examples/skills/adding-resource/SKILL.md` |
| **searching-context** | Search memories and resources semantically | `ovs` | `examples/skills/searching-context/SKILL.md` |

### What Each Skill Does

**adding-memory (ovm)**
- Extracts and stores valuable insights from conversations
- Builds persistent user profile, preferences, and learned patterns
- Triggered by saying "ovm" or when agents identify memorable content
- **Usage:** During chat, provide the keyword `ovm` to trigger memory extraction. The skill concludes previous context and runs `ov add-memory` automatically.

**adding-resource (ovr)**
- Imports external content (files, URLs, directories) into OpenViking
- Automatically processes with semantic analysis
- Triggered by saying "ovr" or when agents encounter useful external content
- **Usage:** During chat, provide the keyword `ovr` to trigger resource import. The skill processes the resource and runs `ov add-resource` automatically.

**searching-context (ovs)**
- Performs semantic search across all stored memories and resources
- Combines vector similarity with directory-aware retrieval
- Triggered by saying "ovs" or when agents need to recall information
- **Usage:** During chat, provide the keyword `ovs` to trigger context search. The skill runs `ov search` automatically with the query context.

### Installing Skills

Skills are installed by copying their documentation to your agent's skill directory:

```bash
# Create skills directory
mkdir -p ~/.openclaw/skills

# Copy skill files (example paths, adjust based on your agent setup)
cp examples/skills/adding-memory/SKILL.md ~/.openclaw/skills/adding-memory/
cp examples/skills/adding-resource/SKILL.md ~/.openclaw/skills/adding-resource/
cp examples/skills/searching-context/SKILL.md ~/.openclaw/skills/searching-context/
```

**For agents:** Point your agent to the skill files in `examples/skills/`. The skill documentation contains the full specification for how to use each capability.

### Using Skills

Once skills are installed and the server is running:

```bash
# Adding memory
ov add-memory "User prefers Python for data processing tasks"

# Adding resource
ov add-resource https://example.com/docs --wait

# Searching context
ov search "Python data processing"
```

**Chat-based skill triggers:**

```
User: I prefer using vim over IDE for coding
User: ovm                    ← Triggers adding-memory skill
                                (extracts and stores this preference)

User: Please add this doc https://example.com/api
User: ovr                    ← Triggers adding-resource skill
                                (imports and processes the URL)

User: ovs What was my editor preference?  ← Triggers searching-context skill
                                           (searches for stored memories)
```

**Note:** Skills require server mode to function properly. The server processes semantic operations significantly faster than embedded mode.

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
    "workspace": "./data",
    "vectordb": {
      "name": "context",
      "backend": "local",
      "project": "default"
    },
    "agfs": {
      "port": 1833,
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
  "log": {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "output": "stdout",
    "rotation": true,
    "rotation_days": 3
  }
}
```

**Configuration via environment variables:**

Instead of setting `OPENVIKING_CONFIG_FILE` to point to a specific file, you can set the config directory. OpenViking will look for `ov.conf` in that directory:

```bash
# Linux/macOS - Set config directory
export OPENVIKING_CONFIG_DIR=~/.openviking

# Or set specific config file
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf

# Windows (PowerShell)
$env:OPENVIKING_CONFIG_DIR = "$HOME/.openviking"

# Windows (CMD)
set "OPENVIKING_CONFIG_DIR=%USERPROFILE%\.openviking"
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

**Note:** If your server runs on a different machine (cloud VM, remote server), update the URL accordingly:

```json
{
  "url": "http://your-server-ip:1933",
  "api_key": "your-api-key"
}
```

**Alternative config location:**

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

---

## Running the Server

Start the OpenViking server (required for skills and CLI):

```bash
# Using uv (recommended) - automatically uses virtual environment
uv run openviking-server

# Or using Python module with uv
uv run python -m openviking serve

# With custom config
uv run python -m openviking serve --config /path/to/ov.conf --port 1933
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:1933
```

### Deployment Options

The server can run **anywhere** - choose what fits your setup:

| Deployment | Use Case | Setup |
|------------|----------|-------|
| **Local machine** | Personal development | `uv run openviking-server` |
| **Cloud VM** | Shared team resource | Deploy to AWS/GCP/Azure/Volcengine ECS |
| **Container** | Scalable deployment | Docker with `docker run` or Kubernetes |
| **Dedicated server** | Production workloads | Bare metal or VM with persistent storage |

**Multi-client access:** Multiple devices (PCs, laptops, mobile) can connect to a single OpenViking server:

```
┌──────────────────────────────────────────────┐
│          OpenViking Server                   │
│         (Cloud VM / Container)               │
│              203.0.113.1:1933                │
└──────────────────────────────────────────────┘
        │           │           │
   ┌────▼───┐  ┌────▼───┐  ┌────▼───┐
   │ov CLI  │  │ov CLI  │  │ov CLI  │
   │(Work PC)│  │(Laptop)│  │(Home PC)│
   └─────────┘  └─────────┘  └─────────┘
```

Each client uses the same `ovcli.conf` pointing to the shared server URL.

**Cloud deployment guide:** See `docs/en/getting-started/03-quickstart-server.md` for detailed Volcengine ECS setup.

---

## Verification

### 1. Verify Python Package

```bash
uv run python -c "
import openviking as ov
print(f'OpenViking version: {ov.__version__}')
print('Python package installed successfully!')
"
```

### 2. Verify Server Health

```bash
curl http://localhost:1933/health
```

Expected output:
```json
{"status": "ok"}
```

### 3. Verify CLI Connection

```bash
# Check version
ov --version

# Check system status
ov system health

# List resources
ov ls

# Show config
ov config show
```

### 4. Full Integration Test

```bash
# Add a test resource
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md --wait

# List resources
ov ls viking://resources

# Search
ov find "what is openviking"
```

### 5. Verify Skill Functionality

Test each installed skill:

```bash
# Test adding-memory (ovm)
ov add-memory "Test memory: User verification complete"
ov search "verification"

# Test adding-resource (ovr)
ov add-resource https://raw.githubusercontent.com/volcengine/OpenViking/main/LICENSE --wait
ov ls viking://resources

# Test searching-context (ovs)
ov search "Apache license"
```

---

## Troubleshooting

### Common Issues

#### 1. Import Error: No module named 'openviking'

**Cause:** Package not installed or wrong Python environment.

**Solution:**
```bash
# Using uv (the only supported method)
uv pip install openviking
```

#### 2. ov: command not found

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

#### 3. Connection refused when connecting to server

**Cause:** Server not running or wrong URL.

**Solution:**
```bash
# Check if server is running
ps aux | grep openviking-server

# Start server with uv
uv run openviking-server

# Verify URL in ovcli.conf matches server host/port
```

#### 4. API key errors

**Cause:** Invalid or missing API key in configuration.

**Solution:**
- Check `api_key` in `~/.openviking/ov.conf`
- Verify key is valid with your model provider
- Check `api_base` URL is correct

#### 5. Embedding model errors

**Cause:** Wrong dimension or model name.

**Solution:**
- Verify `dimension` matches your embedding model
- Common dimensions: 1024 (Doubao), 3072 (OpenAI text-embedding-3-large)
- Check `input: "multimodal"` for vision embedding models

#### 6. Skills not responding

**Cause:** Server not running or skills not properly configured.

**Solution:**
- Verify server is running: `ov system health`
- Check skill files are in the correct location
- Ensure `ovcli.conf` points to the correct server URL

### Getting Help

- **Documentation:** https://www.openviking.ai/docs
- **GitHub Issues:** https://github.com/volcengine/OpenViking/issues
- **Discord:** https://discord.com/invite/eHvx8E9XF3

---

## Quick Reference

| Task | Command |
|------|---------|
| Install package | `uv pip install openviking` |
| Start server | `uv run openviking-server` |
| Check health | `curl http://localhost:1933/health` |
| Add memory | `ov add-memory "content"` |
| Add resource | `ov add-resource <path/URL> --wait` |
| Search | `ov search "query"` |
| List resources | `ov ls viking://resources` |
| System status | `ov system health` |
| Trigger memory | Say `ovm` in chat |
| Trigger resource | Say `ovr` in chat |
| Trigger search | Say `ovs <query>` in chat |

---

## Summary Checklist

- [ ] uv installed (required)
- [ ] Python 3.10+ installed
- [ ] `uv pip install openviking` completed successfully
- [ ] `ov` CLI installed and in PATH (required)
- [ ] Skills copied to agent skill directory
- [ ] `~/.openviking/ov.conf` created with model credentials
- [ ] `~/.openviking/ovcli.conf` created with server URL
- [ ] `OPENVIKING_CONFIG_DIR` or `OPENVIKING_CONFIG_FILE` environment variable set
- [ ] Server started with `uv run openviking-server`
- [ ] Health endpoint returns `{"status": "ok"}`
- [ ] CLI can connect and execute commands
- [ ] All three skills tested and working
