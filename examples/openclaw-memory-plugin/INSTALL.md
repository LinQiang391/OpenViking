# Install OpenViking Memory for OpenClaw

Give [OpenClaw](https://github.com/openclaw/openclaw) long-term memory powered by [OpenViking](https://github.com/volcengine/OpenViking).

After setup, OpenClaw will automatically **remember** facts from conversations and **recall** relevant context before responding.

---

## Let OpenClaw Install It For You

Install the skill, then ask OpenClaw to do the rest:

**Linux / macOS:**

```bash
mkdir -p ~/.openclaw/skills/install-openviking-memory
cp examples/openclaw-memory-plugin/skills/install-openviking-memory/SKILL.md \
   ~/.openclaw/skills/install-openviking-memory/
```

**Windows (cmd):**

```cmd
mkdir "%USERPROFILE%\.openclaw\skills\install-openviking-memory"
copy examples\openclaw-memory-plugin\skills\install-openviking-memory\SKILL.md ^
     "%USERPROFILE%\.openclaw\skills\install-openviking-memory\"
```

Then tell OpenClaw: **"Install OpenViking memory"** — it will read the skill and complete the setup automatically.

Or follow the manual steps below.

---

## Prerequisites

| Requirement | Check | Install |
|-------------|-------|---------|
| **Node.js** >= 18 | `node -v` | [nodejs.org](https://nodejs.org/) |
| **Python** >= 3.10 | `python3 --version` (Linux/macOS) or `python --version` (Windows) | [python.org](https://www.python.org/) |
| **Volcengine Ark API Key** | —| [volcengine.com/product/doubao](https://www.volcengine.com/product/doubao) |

> **Note:** Go is **not** required. The default storage backend (`local`) uses pure Python.

---

## Step 1 — Install OpenClaw

```bash
npm install -g openclaw
```

Run the interactive onboarding to configure your LLM model and API key:

```bash
openclaw onboard
```

Verify:

```bash
openclaw --version
```

---

## Step 2 — Clone OpenViking & Install

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
```

Install the `openviking` Python package (use your Python 3.10+ interpreter):

**Linux / macOS:**

```bash
python3 -m pip install -e .
```

**Windows:**

```powershell
python -m pip install -e .
```

> **Tip:** If you have multiple Python versions, use the full path, e.g. `/path/to/python3.11 -m pip install -e .`

Verify:

**Linux / macOS:**

```bash
python3 -c "import openviking; print('ok')"
```

**Windows:**

```powershell
python -c "import openviking; print('ok')"
```

---

## Step 3 — Run the Setup Helper

From the OpenViking repo root:

```bash
npx ./examples/openclaw-memory-plugin/setup-helper
```

The helper will interactively:

1. **Check** Python, openviking module, and OpenClaw
2. **Create** `~/.openviking/ov.conf` — prompts for your Volcengine Ark API Key, VLM model, and ports
3. **Deploy** the `memory-openviking` plugin into OpenClaw
4. **Configure** OpenClaw to use the plugin in local mode
5. **Write** `~/.openclaw/openviking.env` (Linux/macOS) or `~/.openclaw/openviking.env.bat` (Windows) with Python/Go paths

Example session:

```
🦞 OpenClaw + OpenViking setup helper

ℹ Checking environment...
✓ Python: 3.11 (python3)
✓ openviking module: installed
⚠ Go: not found (optional)
✓ OpenClaw: installed

Volcengine Ark API Key: ********
VLM model [doubao-seed-1-8-251228]:
OpenViking HTTP port [1933]:
AGFS port [1833]:
✓ Created config: /home/user/.openviking/ov.conf
✓ Using local plugin: /home/user/OpenViking/examples/openclaw-memory-plugin
✓ OpenClaw plugin config done
✓ Written ~/.openclaw/openviking.env

✓ Setup complete!
```

---

## Step 4 — Start OpenClaw

**Important:** Load the env file first so the plugin can find the correct Python:

**Linux / macOS:**

```bash
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

Wait a few seconds. You should see:

```
[gateway] listening on ws://127.0.0.1:18789
[gateway] memory-openviking: local server started (http://127.0.0.1:1933, config: ...)
```

The second line confirms OpenViking is running and connected.

> **Convenience (Linux/macOS):** Add this alias to your `~/.bashrc`:
> ```bash
> alias openclaw-start='source ~/.openclaw/openviking.env && openclaw gateway'
> ```

---

## Verify It Works

### Check plugin status

```bash
openclaw status
```

The **Memory** line should show: `enabled (plugin memory-openviking)`

### Test memory

Open the OpenClaw TUI or send a test message:

```bash
openclaw tui
```

Then say something like:

> "Please remember: my favorite programming language is Python."

In a later conversation, ask:

> "What is my favorite programming language?"

OpenClaw should recall the answer from OpenViking memory.

---

## Daily Usage

Each time you want to use OpenClaw with memory:

**Linux / macOS:**

```bash
cd /path/to/OpenViking
source ~/.openclaw/openviking.env && openclaw gateway
```

**Windows (cmd):**

```cmd
cd D:\path\to\OpenViking
call "%USERPROFILE%\.openclaw\openviking.env.bat" && openclaw gateway
```

That's it. The plugin automatically starts and stops the OpenViking server.

---

## Configuration Reference

### `~/.openviking/ov.conf`

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  },
  "storage": {
    "workspace": "~/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local", "port": 1833 }
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
  },
  "vlm": {
    "backend": "volcengine",
    "api_key": "<your-api-key>",
    "model": "doubao-seed-1-8-251228",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.1,
    "max_retries": 3
  }
}
```

### `~/.openclaw/openviking.env` (Linux/macOS) / `openviking.env.bat` (Windows)

Auto-generated by the setup helper. Contains:

**Linux / macOS (`openviking.env`):**

```bash
export OPENVIKING_PYTHON='/path/to/python3'
export OPENVIKING_GO_PATH='/path/to/go/bin'  # optional
```

**Windows (`openviking.env.bat`):**

```cmd
set OPENVIKING_PYTHON=C:\path\to\python.exe
set OPENVIKING_GO_PATH=C:\path\to\go\bin
```

### Setup helper options

```
npx openclaw-openviking-setup-helper [options]

  -y, --yes     Non-interactive, use defaults
  -h, --help    Show help

Environment variables:
  OPENVIKING_PYTHON       Python interpreter path
  OPENVIKING_CONFIG_FILE  Custom ov.conf path
  OPENVIKING_REPO         Local repo path (auto-detected when run from repo)
  OPENVIKING_ARK_API_KEY  Skip API key prompt (for CI/scripts)
```

---

## Troubleshooting

### Plugin not showing in gateway output

- Did you load the env file before `openclaw gateway`?
  - Linux/macOS: `source ~/.openclaw/openviking.env`
  - Windows: `call "%USERPROFILE%\.openclaw\openviking.env.bat"`
- Run `openclaw status` — Memory should show `memory-openviking`
- Re-run setup: `npx ./examples/openclaw-memory-plugin/setup-helper`

### `health check timeout at http://127.0.0.1:1933`

A stale process may be occupying the port. Kill it and restart:

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

### `extracted 0 memories`

The VLM or embedding config in `ov.conf` is incorrect. Verify:

- `embedding.dense.api_key` is a valid Volcengine Ark API key
- `vlm.api_key` is set (usually the same key)
- `vlm.model` is a model name (e.g. `doubao-seed-1-8-251228`), **not** the API key

### Wrong Python version

If `pip install -e .` installs to the wrong Python, use the explicit path:

**Linux / macOS:**

```bash
/path/to/python3.11 -m pip install -e .
export OPENVIKING_PYTHON=/path/to/python3.11
npx ./examples/openclaw-memory-plugin/setup-helper
```

**Windows:**

```cmd
C:\path\to\python.exe -m pip install -e .
set OPENVIKING_PYTHON=C:\path\to\python.exe
npx ./examples/openclaw-memory-plugin/setup-helper
```

---

## Uninstall

**Linux / macOS:**

```bash
# Stop gateway (Ctrl+C or):
lsof -ti tcp:1933 tcp:1833 tcp:18789 | xargs kill -9

# Remove OpenClaw
npm uninstall -g openclaw
rm -rf ~/.openclaw

# Remove OpenViking config & data
python3 -m pip uninstall openviking -y
rm -rf ~/.openviking
```

**Windows (cmd):**

```cmd
REM Stop gateway (Ctrl+C or):
for /f "tokens=5" %a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":1933 :1833 :18789"') do taskkill /PID %a /F

REM Remove OpenClaw
npm uninstall -g openclaw
rmdir /s /q "%USERPROFILE%\.openclaw"

REM Remove OpenViking config & data
python -m pip uninstall openviking -y
rmdir /s /q "%USERPROFILE%\.openviking"
```
