---
name: install-openviking-memory
description: Install and configure OpenViking as the long-term memory backend for OpenClaw. Run this skill once to set up persistent memory that automatically remembers and recalls context across conversations. Trigger when the user asks to "install openviking", "set up memory", or "enable long-term memory".
compatibility: OpenClaw installed (npm install -g openclaw), Python >= 3.10
---

# Install OpenViking Memory for OpenClaw

This skill installs [OpenViking](https://github.com/volcengine/OpenViking) as the memory backend for OpenClaw. After installation, OpenClaw will automatically **remember** facts from conversations and **recall** relevant context.

> 🤖 **For AI Agents reading this:** Follow the steps below in order. Run each command and check the output before proceeding. Ask the user for input only where indicated.

## Step 1: Check Prerequisites

Run these checks first. All must pass before continuing.

**Linux / macOS:**

```bash
# Check Node.js >= 18
node -v

# Check Python >= 3.10 (try multiple paths)
python3 --version || /usr/bin/python3 --version

# Check OpenClaw is installed
openclaw --version
```

**Windows (PowerShell / cmd):**

```powershell
node -v
python --version
openclaw --version
```

If Python is not >= 3.10, stop and tell the user:
> "Python 3.10+ is required. Please install it first: https://www.python.org/"

If OpenClaw is not installed, stop and tell the user:
> "OpenClaw is required. Install it with: `npm install -g openclaw`"

## Step 2: Clone and Install OpenViking

If the OpenViking repo is not already cloned:

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
```

Install the Python package (use the Python 3.10+ interpreter found in Step 1):

**Linux / macOS:**

```bash
python3 -m pip install -e .
```

**Windows:**

```powershell
python -m pip install -e .
```

Verify:

**Linux / macOS:**

```bash
python3 -c "import openviking; print('openviking module: ok')"
```

**Windows:**

```powershell
python -c "import openviking; print('openviking module: ok')"
```

If the import fails, the user may have multiple Python versions. Ask which Python to use, then install with that path:

```bash
/path/to/python3.11 -m pip install -e .
```

## Step 3: Run the Setup Helper

From the OpenViking repo root, run the interactive setup helper:

```bash
npx ./examples/openclaw-memory-plugin/setup-helper
```

The helper will prompt for:

1. **Volcengine Ark API Key** — ask the user to provide this. It's required for embedding and VLM models.
2. **VLM model** — default `doubao-seed-1-8-251228`, press Enter to accept.
3. **OpenViking HTTP port** — default `1933`, press Enter to accept.
4. **AGFS port** — default `1833`, press Enter to accept.

> **Important:** If the user doesn't have a Volcengine API key, direct them to: https://www.volcengine.com/product/doubao

The helper will automatically:
- Create `~/.openviking/ov.conf`
- Deploy the memory-openviking plugin into OpenClaw
- Configure OpenClaw to use local mode
- Write `~/.openclaw/openviking.env` (Linux/macOS) or `~/.openclaw/openviking.env.bat` (Windows)

Wait for `✅ Setup complete!` before proceeding.

## Step 4: Start OpenClaw with Memory

**Always load the env file first**, then start the gateway:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

Wait a few seconds. Verify in the output that this line appears:

```
[gateway] memory-openviking: local server started (http://127.0.0.1:1933, ...)
```

If it does, the installation is complete. Tell the user:
> "OpenViking memory is now active. I will automatically remember important facts from our conversations and recall them when relevant."

## Step 5: Verify (Optional)

Check the plugin status:

```bash
openclaw status
```

The **Memory** line should show: `enabled (plugin memory-openviking)`

## Troubleshooting

### `health check timeout at http://127.0.0.1:1933`

A stale process is blocking the port. Fix:

**Linux / macOS:**

```bash
lsof -ti tcp:1933 tcp:1833 | xargs kill -9
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833"') do taskkill /PID %a /F
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

### Plugin not loaded (no memory-openviking in gateway output)

Re-run the setup helper:

```bash
npx ./examples/openclaw-memory-plugin/setup-helper
```

Then restart the gateway with env:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

### `extracted 0 memories`

Check `~/.openviking/ov.conf`:
- `embedding.dense.api_key` must be a valid Volcengine Ark API key
- `vlm.model` must be a model name (e.g. `doubao-seed-1-8-251228`), not the API key

### Wrong Python version

Set the correct Python explicitly:

**Linux / macOS:**

```bash
export OPENVIKING_PYTHON=/path/to/python3.11
npx ./examples/openclaw-memory-plugin/setup-helper
```

**Windows (cmd):**

```cmd
set OPENVIKING_PYTHON=C:\path\to\python.exe
npx ./examples/openclaw-memory-plugin/setup-helper
```

## Daily Usage

Each time the user wants to start OpenClaw with memory:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

Or suggest adding an alias:

```bash
echo 'alias openclaw-start="source ~/.openclaw/openviking.env && openclaw gateway"' >> ~/.bashrc
source ~/.bashrc
openclaw-start
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

## Uninstall

**Linux / macOS:**

```bash
# Stop all services
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9

# Remove OpenClaw
npm uninstall -g openclaw
rm -rf ~/.openclaw

# Remove OpenViking
python3 -m pip uninstall openviking -y
rm -rf ~/.openviking
```

**Windows (cmd):**

```cmd
REM Stop all services
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833 :18789"') do taskkill /PID %a /F

REM Remove OpenClaw
npm uninstall -g openclaw
rmdir /s /q "%USERPROFILE%\.openclaw"

REM Remove OpenViking
python -m pip uninstall openviking -y
rmdir /s /q "%USERPROFILE%\.openviking"
```
