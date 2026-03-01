# OpenClaw + OpenViking Memory Plugin

Use OpenViking as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw).

## Quick Start (No Source Download)

Install with one command (installer can auto-install missing Node.js, OpenClaw, Python, and build tools):

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.sh | bash

# Linux / macOS (recommended: explicitly pin helper download ref)
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.sh | OV_MEMORY_VERSION=ocm@<version> bash
```

```powershell
# Windows PowerShell
iwr https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.ps1 -UseBasicParsing | iex

# Windows PowerShell (recommended: explicitly pin helper download ref)
$env:OV_MEMORY_VERSION='ocm@<version>'; iwr https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.ps1 -UseBasicParsing | iex
```

Non-interactive mode example:

```bash
OPENVIKING_ARK_API_KEY=<your-api-key> \
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.sh \
| OV_MEMORY_VERSION=ocm@<version> bash -s -- -y
```

## Quick Start (From Local Repo)

```bash
cd /path/to/OpenViking
npx ./examples/openclaw-memory-plugin/setup-helper
openclaw gateway
```

The setup helper checks the environment, creates `~/.openviking/ov.conf`, deploys the plugin, and configures OpenClaw automatically.

## Manual Setup

Prerequisites: **OpenClaw** (`npm install -g openclaw`), **Python >= 3.10** with `openviking` (`pip install openviking`).

```bash
# Install plugin
mkdir -p ~/.openclaw/extensions/memory-openviking
cp examples/openclaw-memory-plugin/{index.ts,config.ts,openclaw.plugin.json,package.json,.gitignore} \
   ~/.openclaw/extensions/memory-openviking/
cd ~/.openclaw/extensions/memory-openviking && npm install

# Configure (local mode — plugin auto-starts OpenViking)
openclaw config set plugins.enabled true
openclaw config set plugins.slots.memory memory-openviking
openclaw config set plugins.entries.memory-openviking.config.mode "local"
openclaw config set plugins.entries.memory-openviking.config.configPath "~/.openviking/ov.conf"
openclaw config set plugins.entries.memory-openviking.config.targetUri "viking://"
openclaw config set plugins.entries.memory-openviking.config.autoRecall true --json
openclaw config set plugins.entries.memory-openviking.config.autoCapture true --json

# Start
openclaw gateway
```

## Setup Helper Options

```
npx openclaw-openviking-setup-helper [options]

  -y, --yes     Non-interactive, use defaults
  -h, --help    Show help

Env vars:
  OPENVIKING_PYTHON       Python path
  OPENVIKING_CONFIG_FILE  ov.conf path
  OPENVIKING_REPO         Local OpenViking repo path
  OPENVIKING_ARK_API_KEY  Volcengine API Key (skip prompt in -y mode)
```

## Bootstrap Installer Env Vars

```bash
OV_MEMORY_VERSION      # Pin setup-helper download ref (for example: ocm@0.1.0)
OV_MEMORY_REPO         # Override GitHub repo (default: volcengine/OpenViking)
AUTO_INSTALL_NODE=1    # Auto-install Node.js when missing/too old (default: 1)
AUTO_INSTALL_PYTHON=1  # Auto-install Python >=3.10 when missing/too old (default: 1)
AUTO_INSTALL_XPM=1     # Try xpm for missing deps before other methods (default: 1)
OV_MEMORY_NODE_VERSION # Node.js version used by auto-install (default: 22)
OPENVIKING_GITHUB_RAW  # Override raw base URL used by installer/helper
SKIP_CHECKSUM=1        # Skip checksum verification (not recommended)
```

## ov.conf Example

```json
{
  "vlm": {
    "backend": "volcengine",
    "api_key": "<your-api-key>",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  },
  "embedding": {
    "dense": {
      "backend": "volcengine",
      "api_key": "<your-api-key>",
      "model": "doubao-embedding-vision-250615",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  }
}
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Memory shows `disabled` / `memory-core` | `openclaw config set plugins.slots.memory memory-openviking` |
| `memory_store failed: fetch failed` | Check OpenViking is running; verify `ov.conf` and Python path |
| `health check timeout` | `lsof -ti tcp:1933 \| xargs kill -9` then restart |
| `extracted 0 memories` | Ensure `ov.conf` has valid `vlm` and `embedding.dense` with API key |
