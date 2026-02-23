# OpenViking Installation Guide

Quick installation guide for OpenViking - the Context Database for AI Agents.

**Goal:** Get OpenViking running with skills in 5 minutes.

---

## Quick Install

### 1. Install uv

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Install OpenViking Server

```bash
uv tool install openviking
```

This installs `openviking-server` as a standalone tool you can run anywhere.

### 3. Install ov CLI (Required for Skills)

```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash
```

Or install from source:
```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
cargo install --path crates/ov_cli
```

### 4. Configure and Start Server

Create config directory and config file:

```bash
mkdir -p ~/.openviking

cat > ~/.openviking/ov.conf << 'EOF'
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
  },
  "storage": {
    "workspace": "~/.openviking/data"
  }
}
EOF
```

**Replace `your-api-key` with your actual API key.**

> **For AI agents:** Before creating the config, prompt the user for their preferred model provider and API credentials. Required: embedding provider/model/api_key/api_base, and VLM provider/model/api_key/api_base. Use the user's provided values instead of the placeholder defaults above.

Start the server in background:

```bash
nohup openviking-server > ~/.openviking/server.log 2>&1 &
```

Check it's running:
```bash
curl http://localhost:1933/health
# Should return: {"status": "ok"}
```

### 5. Configure CLI

```bash
cat > ~/.openviking/ovcli.conf << 'EOF'
{
  "url": "http://localhost:1933"
}
EOF
```

### 6. Install Skills

Skills enable agents to use OpenViking. Install them to your agent's skill directory:

```bash
mkdir -p ~/.openclaw/skills

# Copy skills from OpenViking examples
cp -r /path/to/openviking/examples/skills/adding-memory ~/.openclaw/skills/
cp -r /path/to/openviking/examples/skills/adding-resource ~/.openclaw/skills/
cp -r /path/to/openviking/examples/skills/searching-context ~/.openclaw/skills/
```

Or download directly:
```bash
mkdir -p ~/.openclaw/skills/adding-memory
curl -o ~/.openclaw/skills/adding-memory/SKILL.md \
  https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/skills/adding-memory/SKILL.md

mkdir -p ~/.openclaw/skills/adding-resource
curl -o ~/.openclaw/skills/adding-resource/SKILL.md \
  https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/skills/adding-resource/SKILL.md

mkdir -p ~/.openclaw/skills/searching-context
curl -o ~/.openclaw/skills/searching-context/SKILL.md \
  https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/skills/searching-context/SKILL.md
```

---

## Using Skills

Once installed, agents can use these keywords during chat:

| Keyword | Skill | What It Does |
|---------|-------|--------------|
| `ovm` | adding-memory | Extracts and stores insights from conversation |
| `ovr` | adding-resource | Imports files/URLs into OpenViking |
| `ovs` | searching-context | Searches stored memories and resources |

**Example chat flow:**
```
User: I prefer using vim for coding
User: ovm
→ Agent extracts and stores: "User prefers vim for coding"

User: Please add https://example.com/docs
User: ovr
→ Agent imports and processes the URL

User: ovs What's my editor preference?
→ Agent searches and returns: "User prefers vim for coding"
```

---

## Quick Test

Verify everything works:

```bash
# Test CLI connection
ov system health

# Test adding memory
ov add-memory "Test: OpenViking is working"

# Test searching
ov search "OpenViking working"
```

---

## Advanced Configuration

For advanced setup options (cloud deployment, custom storage, multiple model providers, etc.), see:

**[INSTALLATION_ADVANCED.md](./INSTALLATION_ADVANCED.md)**

This includes:
- Full configuration reference
- Cloud deployment guides
- Docker/container setup
- Multiple model providers
- Authentication and security
- Troubleshooting deep dives

---

## Requirements

- Python 3.10+
- Rust (for building ov CLI)
- API keys for VLM and embedding models

**Supported Model Providers:** Volcengine, OpenAI, Anthropic, DeepSeek, Google, Moonshot, Zhipu, DashScope, MiniMax, OpenRouter, vLLM

---

## Quick Reference

```bash
# Install
uv tool install openviking
curl -fsSL .../install.sh | bash  # ov CLI

# Start server (background)
nohup openviking-server > ~/.openviking/server.log 2>&1 &

# Stop server
pkill openviking-server

# CLI commands
ov system health          # Check server
ov add-memory "text"      # Add memory
ov add-resource <URL>     # Add resource
ov search "query"         # Search context
```
