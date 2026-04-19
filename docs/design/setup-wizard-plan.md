# OpenViking 插件 Setup Wizard 实现计划

## 背景

当前 `openclaw plugins install clawhub:@openclaw/openviking` 只完成了插件文件的部署，但没有引导用户完成必要的配置。用户需要手动编辑 `openclaw.json` 来设置 mode、configPath、port、baseUrl 等参数。

参考 [openclaw-honcho](https://github.com/plastic-labs/openclaw-honcho) 的做法：通过 `api.registerCli()` 注册 `openclaw honcho setup` 命令，在 CLI 中提供交互式配置向导。

## 目标

实现 `openclaw openviking setup` 命令，提供交互式配置向导，需要：

1. **区分首次安装 vs 升级**：首次安装走完整配置流程；已有配置的升级场景保留现有配置
2. **local 模式**：检测本地 OpenViking 服务是否已安装（Python + openviking 包），未安装则提示安装方法
3. **remote 模式**：检测远程 OpenViking 服务的连通性，验证 URL 是否可达

## 实现步骤

### Step 1：创建 `commands/setup.ts` 文件

新建 `examples/openclaw-plugin/commands/setup.ts`，实现 setup 向导核心逻辑。

**文件结构**：
```
examples/openclaw-plugin/
├── commands/
│   └── setup.ts          ← 新增
├── index.ts              ← 修改（注册 CLI）
├── config.ts
├── ...
```

### Step 2：实现 setup 向导的交互流程

```
openclaw openviking setup [--reconfigure] [--zh]
```

**完整流程**：

```
┌─────────────────────────────────────────────────┐
│  openclaw openviking setup                       │
└──────────────────────┬──────────────────────────┘
                       │
                ┌──────▼──────┐
                │ 读取现有配置  │
                │ openclaw.json│
                └──────┬──────┘
                       │
              ┌────────▼────────┐
              │ 是否已有配置？    │
              └───┬─────────┬───┘
                  │         │
             有配置│         │无配置
                  │         │
         ┌────────▼────┐    │
         │ 显示当前配置  │    │
         │ 是否重新配置？│    │
         └──┬──────┬───┘    │
            │      │        │
         保留│   重配│        │
            │      │        │
            │  ┌───▼────────▼───┐
            │  │ 选择 mode       │
            │  │ local / remote  │
            │  └──┬──────────┬──┘
            │     │          │
            │  local│      remote│
            │     │          │
            │  ┌──▼──────┐ ┌▼──────────────┐
            │  │检测 Python│ │输入 remote URL │
            │  │检测 OV包  │ │输入 API Key    │
            │  │配置 ov.conf│ │输入 Agent ID  │
            │  └──┬──────┘ └┬──────────────┘
            │     │          │
            │  ┌──▼──────┐ ┌▼──────────────┐
            │  │配置端口   │ │测试连通性      │
            │  │API Keys  │ │GET /health     │
            │  └──┬──────┘ └┬──────────────┘
            │     │          │
            └─────┼──────────┘
                  │
           ┌──────▼───────┐
           │ 写入 openclaw │
           │ .json 配置    │
           └──────┬───────┘
                  │
           ┌──────▼───────┐
           │ 验证 & 总结   │
           └──────────────┘
```

### Step 3：首次安装 vs 升级检测

**检测逻辑**：

```typescript
// 读取 ~/.openclaw/openclaw.json
const config = readOpenClawConfig();
const existingEntry = config?.plugins?.entries?.openviking?.config;

if (existingEntry && existingEntry.mode) {
  // 升级场景：已有配置
  // 显示当前配置摘要，询问是否重新配置
  // 默认保留现有配置
} else {
  // 首次安装：无配置
  // 进入完整配置向导
}
```

**升级场景行为**：
- 显示当前配置（mode、baseUrl/configPath、port 等）
- 默认按 Enter 保留现有值
- 加 `--reconfigure` 强制重新配置所有项

### Step 4：local 模式环境检测（阻断式）

当用户选择 local 模式时，**必须**依次检测以下条件。任一检测失败则**阻断流程**，提示用户先完成安装，再重新运行 setup。

#### 4.1 检测 Python

```
检查: python3 --version (Linux/Mac) 或 python --version (Windows)
要求: >= 3.10
```

**失败时阻断输出**：
```
✗ Python 未找到或版本过低（需要 >= 3.10）

请先安装 Python 3.10+：
  Linux:   pyenv install 3.11.12 && pyenv global 3.11.12
  macOS:   brew install python@3.11
  Windows: winget install --id Python.Python.3.11 -e

安装完成后重新运行：
  openclaw openviking setup
```

#### 4.2 检测 OpenViking 服务包

```
运行: python3 -c "import openviking; print(openviking.__version__)"
```

**失败时阻断输出**：
```
✗ OpenViking service is not installed

Please install the OpenViking server first:

  pip install openviking

  For more details, see:
    https://github.com/volcengine/OpenViking#installation

After installation, run setup again to configure the plugin:
  openclaw openviking setup
```

> **注意**：这里只推荐 `pip install openviking` 安装 OV 服务。不推荐 `ov-install` 一键安装脚本，因为 `ov-install` 会连带重新安装插件，覆盖用户已通过 ClawHub 安装的版本。

> **关键**：此处不继续配置流程，直接 `process.exit(1)`。用户安装完 OpenViking 后再次运行 setup，会重新进入配置向导。

#### 4.3 交互式配置（轻量）

local 模式下插件本身的配置项很少，都有合理默认值：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `configPath` | `~/.openviking/ov.conf` | OV 服务配置路径，大多数用户无需修改 |
| `port` | `1933` | 本地服务端口，大多数用户无需修改 |

交互提示：
```
Config path [~/.openviking/ov.conf]: <Enter 使用默认>
Port [1933]: <Enter 使用默认>
```

> **注意**：不涉及 ov.conf 的生成。OV 服务的配置（workspace、VLM API keys、embedding 等）由 OV 服务自身安装流程管理，不在插件 setup 范围内。

#### 4.4 检测服务运行状态（非阻断，仅提示）

```
尝试: GET http://127.0.0.1:<port>/health
```

- 成功 → `✓ OpenViking 服务已运行 (version: xxx)`
- 失败 → `ℹ OpenViking service is not running on port <port> (will auto-start with openclaw gateway)`
  此处**不阻断**，因为 local 模式下 OpenClaw 插件会在 gateway 启动时自动拉起 OpenViking 服务，用户无需手动启动。

### Step 5：remote 模式连通性检测

当用户选择 remote 模式时：

1. **输入配置**：
   - OpenViking server URL（默认 `http://127.0.0.1:1933`）
   - API Key（可选）
   - Agent ID（可选）

2. **连通性测试**：
   ```typescript
   const response = await fetch(`${baseUrl}/health`, {
     headers: apiKey ? { "Authorization": `Bearer ${apiKey}` } : {},
     signal: AbortSignal.timeout(10000),
   });
   ```
   - 成功 → `✓ OpenViking 服务连接成功 (version: xxx)`
   - 失败 → `✗ 无法连接到 OpenViking 服务：<错误信息>`，但仍允许继续配置

### Step 6：写入配置

**直接读写 `openclaw.json` 文件**（参考 honcho 做法），一次性写入所有配置，避免多次 `openclaw config set` 导致 gateway 不断重启。

```typescript
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

const configDir = path.join(os.homedir(), ".openclaw");
const configPath = path.join(configDir, "openclaw.json");

// 读取现有配置
let config: Record<string, unknown> = {};
if (fs.existsSync(configPath)) {
  try { config = JSON.parse(fs.readFileSync(configPath, "utf-8")); } catch {}
}

// 确保 plugins.entries 结构存在
if (!config.plugins) config.plugins = {};
const plugins = config.plugins as Record<string, unknown>;
if (!plugins.entries) plugins.entries = {};
const entries = plugins.entries as Record<string, unknown>;

// 保留现有 entry 中的其他字段，只更新 config 部分
const existingEntry = (entries.openviking as Record<string, unknown>) ?? {};
const pluginCfg: Record<string, unknown> = {
  ...(existingEntry.config as Record<string, unknown> ?? {}),
};

// 写入用户选择的配置
pluginCfg.mode = resolvedMode;
if (resolvedMode === "local") {
  pluginCfg.configPath = ovConfPath;
  pluginCfg.port = port;
  delete pluginCfg.baseUrl;
} else {
  pluginCfg.baseUrl = resolvedBaseUrl;
  if (resolvedApiKey) pluginCfg.apiKey = resolvedApiKey;
  if (resolvedAgentId) pluginCfg.agentId = resolvedAgentId;
  delete pluginCfg.configPath;
  delete pluginCfg.port;
}

entries.openviking = { ...existingEntry, config: pluginCfg };

// 一次性写入
if (!fs.existsSync(configDir)) fs.mkdirSync(configDir, { recursive: true });
fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
console.log("\n✓ Configuration saved to ~/.openclaw/openclaw.json");
console.log("Run `openclaw gateway --force` to activate.\n");
```

**要点**：
- 一次 `writeFileSync`，gateway 只需重启一次
- 保留 `openclaw.json` 中非 openviking 的其他配置不变
- 保留 openviking entry 中 config 以外的字段不变

### Step 7：在 index.ts 中注册 CLI

参考 honcho 的 `registerCli` 模式：

```typescript
// index.ts 的 register 方法中添加
import { registerSetupCli } from "./commands/setup.js";

register(api) {
  // ... 现有逻辑 ...
  registerSetupCli(api);
}
```

### Step 8：更新 install-manifest.json

将新文件加入 manifest 的 files 列表：

```json
{
  "files": {
    "optional": [
      "commands/setup.ts",
      // ... 其他
    ]
  }
}
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `commands/setup.ts` | 新增 | setup 向导核心逻辑 |
| `index.ts` | 修改 | 添加 `registerSetupCli(api)` 调用 |
| `install-manifest.json` | 修改 | 添加 `commands/setup.ts` 到文件列表 |
| `openclaw.plugin.json` | 可能修改 | 如需注册 CLI 命令元数据 |

## 关键设计决策

### 1. 为什么用 `registerCli` 而不是独立脚本？

- 与 honcho 保持一致的模式
- 用户体验统一：`openclaw openviking setup`
- 可以复用 OpenClaw 的配置读写机制
- 不需要用户下载额外脚本

### 2. install.js 的关系

`install.js` 负责**完整的一键安装**（Python环境 + pip install openviking + 插件部署 + 配置），适合全新环境。

`openclaw openviking setup` 负责**插件安装后的配置**（通过 ClawHub 安装的用户），更轻量，只处理配置向导。

两者互补：
- 全新安装用户 → `node install.js`（或 `npx ov-install`）
- ClawHub 安装用户 → `openclaw openviking setup`

### 3. 中英文支持

参考 install.js 的 `tr(en, zh)` 模式，`--zh` 参数切换中文提示。默认检测终端 locale 或环境变量。

## 完整交互式命令行场景

### Local 模式场景

#### L1：首次安装 —— 环境正常，服务已运行

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Checking environment...
  Python: 3.11.12 ✓
  OpenViking: 0.3.0 ✓

No existing configuration found. Starting setup wizard.

Plugin mode - local or remote [local]: local

── Local Mode Configuration ──

Config path [~/.openviking/ov.conf]: 
Port [1933]: 

Checking OpenViking service on port 1933...
  ✓ OpenViking service is running (version: 0.3.0)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:       local
  configPath: /home/user/.openviking/ov.conf
  port:       1933

Run `openclaw gateway --force` to activate the plugin.
```

#### L2：首次安装 —— 环境正常，服务未运行

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Checking environment...
  Python: 3.11.12 ✓
  OpenViking: 0.3.0 ✓

No existing configuration found. Starting setup wizard.

Plugin mode - local or remote [local]: local

── Local Mode Configuration ──

Config path [~/.openviking/ov.conf]: 
Port [1933]: 

Checking OpenViking service on port 1933...
  ℹ OpenViking service is not running on port 1933 (will auto-start with openclaw gateway)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:       local
  configPath: /home/user/.openviking/ov.conf
  port:       1933

Run `openclaw gateway --force` to activate the plugin.
```

#### L3：首次安装 —— Python 未安装（阻断）

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Checking environment...
  ✗ Python not found or version too old (need >= 3.10)

Please install Python 3.10+ first:
  Linux:   pyenv install 3.11.12 && pyenv global 3.11.12
  macOS:   brew install python@3.11
  Windows: winget install --id Python.Python.3.11 -e

After installation, run setup again:
  openclaw openviking setup
```

#### L4：首次安装 —— OpenViking 服务未安装（阻断）

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Checking environment...
  Python: 3.11.12 ✓
  ✗ OpenViking service is not installed

Please install the OpenViking server first:

  pip install openviking

  For more details, see:
    https://github.com/volcengine/OpenViking#installation

After installation, run setup again to configure the plugin:
  openclaw openviking setup
```

#### L5：升级场景 —— 已有 local 配置，保留

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Existing configuration found:
  mode:       local
  configPath: /home/user/.openviking/ov.conf
  port:       1933

Press Enter to keep existing values, or use --reconfigure to change.

✓ Using existing configuration

Checking environment...
  Python: 3.11.12 ✓
  OpenViking: 0.3.0 ✓

Checking OpenViking service on port 1933...
  ✓ OpenViking service is running (version: 0.3.0)

✓ Plugin is ready. Run `openclaw gateway --force` to activate.
```

#### L6：升级场景 —— 强制重新配置

```
$ openclaw openviking setup --reconfigure

🦣 OpenViking Plugin Setup

Existing configuration found:
  mode:       local
  configPath: /home/user/.openviking/ov.conf
  port:       1933

Reconfiguring...

Checking environment...
  Python: 3.11.12 ✓
  OpenViking: 0.3.0 ✓

Plugin mode - local or remote [local]: local

── Local Mode Configuration ──

Config path [/home/user/.openviking/ov.conf]: /opt/openviking/ov.conf
Port [1933]: 8080

Checking OpenViking service on port 8080...
  ℹ OpenViking service is not running on port 8080 (will auto-start with openclaw gateway)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:       local
  configPath: /opt/openviking/ov.conf
  port:       8080

Run `openclaw gateway --force` to activate the plugin.
```

#### L7：首次安装 —— 自定义配置路径和端口

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Checking environment...
  Python: 3.11.12 ✓
  OpenViking: 0.3.0 ✓

No existing configuration found. Starting setup wizard.

Plugin mode - local or remote [local]: local

── Local Mode Configuration ──

Config path [~/.openviking/ov.conf]: /data/openviking/my-config.conf
Port [1933]: 9000

Checking OpenViking service on port 9000...
  ℹ OpenViking service is not running on port 9000 (will auto-start with openclaw gateway)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:       local
  configPath: /data/openviking/my-config.conf
  port:       9000

Run `openclaw gateway --force` to activate the plugin.
```

### Remote 模式场景

#### R1：首次安装 —— 连通性测试通过

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

No existing configuration found. Starting setup wizard.

Plugin mode - local or remote [local]: remote

── Remote Mode Configuration ──

OpenViking server URL [http://127.0.0.1:1933]: https://openviking.example.com
API Key (optional): sk-abc123
Agent ID (optional): my-agent

Testing connectivity to https://openviking.example.com...
  ✓ Connected successfully (version: 0.3.0)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:    remote
  baseUrl: https://openviking.example.com
  apiKey:  sk-a...123
  agentId: my-agent

Run `openclaw gateway --force` to activate the plugin.
```

#### R2：首次安装 —— 连通性测试失败（非阻断）

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

No existing configuration found. Starting setup wizard.

Plugin mode - local or remote [local]: remote

── Remote Mode Configuration ──

OpenViking server URL [http://127.0.0.1:1933]: https://openviking.example.com
API Key (optional): sk-abc123
Agent ID (optional): 

Testing connectivity to https://openviking.example.com...
  ✗ Connection failed: ECONNREFUSED

  The configuration will still be saved. Make sure the server is reachable
  before starting the gateway.

Save configuration anyway? [Y/n]: y

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:    remote
  baseUrl: https://openviking.example.com
  apiKey:  sk-a...123

Run `openclaw gateway --force` to activate the plugin.
```

#### R3：首次安装 —— 使用默认值（本地 remote）

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

No existing configuration found. Starting setup wizard.

Plugin mode - local or remote [local]: remote

── Remote Mode Configuration ──

OpenViking server URL [http://127.0.0.1:1933]: 
API Key (optional): 
Agent ID (optional): 

Testing connectivity to http://127.0.0.1:1933...
  ✓ Connected successfully (version: 0.3.0)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:    remote
  baseUrl: http://127.0.0.1:1933

Run `openclaw gateway --force` to activate the plugin.
```

#### R4：升级场景 —— 已有 remote 配置，保留

```
$ openclaw openviking setup

🦣 OpenViking Plugin Setup

Existing configuration found:
  mode:    remote
  baseUrl: https://openviking.example.com
  apiKey:  sk-a...123

Press Enter to keep existing values, or use --reconfigure to change.

✓ Using existing configuration

Testing connectivity to https://openviking.example.com...
  ✓ Connected successfully (version: 0.3.0)

✓ Plugin is ready. Run `openclaw gateway --force` to activate.
```

#### R5：升级场景 —— 从 local 切换到 remote

```
$ openclaw openviking setup --reconfigure

🦣 OpenViking Plugin Setup

Existing configuration found:
  mode:       local
  configPath: /home/user/.openviking/ov.conf
  port:       1933

Reconfiguring...

Plugin mode - local or remote [local]: remote

── Remote Mode Configuration ──

OpenViking server URL [http://127.0.0.1:1933]: https://openviking.mycompany.com
API Key (optional): sk-xyz789
Agent ID (optional): team-agent

Testing connectivity to https://openviking.mycompany.com...
  ✓ Connected successfully (version: 0.3.0)

✓ Configuration saved to ~/.openclaw/openclaw.json

  mode:    remote
  baseUrl: https://openviking.mycompany.com
  apiKey:  sk-x...789
  agentId: team-agent

Run `openclaw gateway --force` to activate the plugin.
```

### 中文模式场景

#### ZH1：首次安装 local 模式（中文）

```
$ openclaw openviking setup --zh

🦣 OpenViking 插件配置向导

正在检查环境...
  Python: 3.11.12 ✓
  OpenViking: 0.3.0 ✓

未找到现有配置，开始配置向导。

插件模式 - local 或 remote [local]: local

── 本地模式配置 ──

配置文件路径 [~/.openviking/ov.conf]: 
端口 [1933]: 

正在检查 OpenViking 服务 (端口 1933)...
  ✓ OpenViking 服务正在运行 (版本: 0.3.0)

✓ 配置已保存至 ~/.openclaw/openclaw.json

  模式:       local
  配置文件:   /home/user/.openviking/ov.conf
  端口:       1933

运行 `openclaw gateway --force` 以激活插件。
```

## 验证检查点

- [ ] `openclaw openviking setup` 首次运行，完成完整配置（场景 L1/L2/R1/R3）
- [ ] `openclaw openviking setup` 已有配置时，显示现有值并提供保留选项（场景 L5/R4）
- [ ] `openclaw openviking setup --reconfigure` 强制重新配置（场景 L6/R5）
- [ ] local 模式：正确检测 Python 和 openviking 包（场景 L1/L2）
- [ ] local 模式：Python 未安装时阻断并提示（场景 L3）
- [ ] local 模式：OpenViking 未安装时阻断并提示（场景 L4）
- [ ] remote 模式：连通性测试通过时显示成功（场景 R1/R3）
- [ ] remote 模式：连接失败时给出错误信息但允许保存（场景 R2）
- [ ] 配置正确写入 `~/.openclaw/openclaw.json`（所有场景）
- [ ] 升级场景不覆盖用户已有配置（场景 L5/R4）
- [ ] 支持 `--zh` 中文模式（场景 ZH1）
- [ ] 支持从 local 切换到 remote 或反向切换（场景 R5）
