# LoCoMo 通用评测框架设计方案

## 1. 背景与目标

当前 `benchmark/locomo/` 下有 `openclaw/`、`vikingbot/`、`mem0/` 三套独立脚本，分别负责不同记忆后端的 LoCoMo 评测。每套脚本有自己的 ingest、QA、judge、stat 逻辑，组合方式靠手工操作，无法一键跑通**多场景对比测试**。

### 目标

提供一个**通用 Python 评测脚本** `run_eval.py`，用户只需填写 `config.yaml`，即可自动完成以下**四种场景**的 **部署 → 注入 → QA → 裁判 → 统计 → 清理** 全流程测试，并输出**对比报告**：

| 场景编号 | 场景名称 | 记忆组合 |
|---------|----------|---------|
| S1 | `openclaw_only` | OpenClaw 纯对话，不接入任何长期记忆 |
| S2 | `openclaw_memcore` | OpenClaw + MemCore（mem0）|
| S3 | `openclaw_openviking` | OpenClaw + OpenViking |
| S4 | `openclaw_openviking_memcore` | OpenClaw + OpenViking + MemCore |

---

## 2. 整体架构

```
config.yaml  ───→  run_eval.py  ───→  测试报告
                       │
                       ├── EnvironmentManager（环境管理器）
                       │     ├── OpenClawSetup（部署 & 插件配置）
                       │     ├── ProfileManager（profile 管理）
                       │     └── CleanupManager（数据清理）
                       │
                       ├── ScenarioRunner（场景运行器）
                       │     ├── SetupPhase（环境准备阶段）
                       │     ├── IngestPhase（注入阶段）
                       │     ├── QAPhase（问答阶段）
                       │     ├── JudgePhase（裁判阶段）
                       │     ├── StatPhase（统计阶段）
                       │     └── CleanupPhase（清理阶段）
                       │
                       └── ReportGenerator（报告生成器）
                             ├── 单场景报告
                             └── 多场景对比报告
```

---

## 3. 完整流程设计（六个阶段）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    run_eval.py 评测流程                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Stage 0: 环境预检 & 部署                                            │
│  ├── 检查 openclaw 版本 (openclaw --version)                         │
│  ├── 检查 OpenViking 服务连通性                                       │
│  ├── 输出当前配置 (openclaw --profile)                                │
│  └── 按场景安装/启用所需插件                                           │
│                                                                     │
│  for each scenario in config.scenarios:                              │
│  │                                                                   │
│  │  Stage 1: 场景环境准备                                             │
│  │  ├── 切换 OpenClaw profile / 配置插件                              │
│  │  ├── 重启 OpenClaw gateway                                        │
│  │  └── 健康检查                                                     │
│  │                                                                   │
│  │  Stage 2: 数据注入 (Ingest)                                       │
│  │  ├── OpenViking 注入（如需要）                                      │
│  │  ├── OpenClaw 会话注入（如需要）                                    │
│  │  ├── MemCore(mem0) 注入（如需要）                                   │
│  │  └── 等待后端索引完成                                               │
│  │                                                                   │
│  │  Stage 3: QA 评测                                                 │
│  │  ├── 并发执行 QA 问题                                              │
│  │  ├── 记录回答 & token 消耗                                         │
│  │  └── 断点续跑（跳过已完成题目）                                      │
│  │                                                                   │
│  │  Stage 4: 裁判打分 (Judge)                                        │
│  │  ├── LLM 异步打分                                                 │
│  │  └── 实时保存结果                                                  │
│  │                                                                   │
│  │  Stage 5: 统计 & 单场景报告                                        │
│  │  ├── 准确率统计（总体 + 按 category）                               │
│  │  ├── Token 消耗统计                                                │
│  │  └── 输出 summary.txt                                             │
│  │                                                                   │
│  end for                                                             │
│                                                                     │
│  Stage 6: 生成跨场景对比报告                                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│          独立操作：环境清理（仅用户手动触发时执行）                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  python run_eval.py --cleanup [--scenario <name>]                   │
│  ├── 清理 OpenClaw session 数据                                      │
│  ├── 清理 OpenViking 记忆数据                                        │
│  ├── 清理 MemCore(mem0) 用户数据                                     │
│  └── ⚠️ 需要用户二次确认，不可逆操作                                   │
│                                                                     │
│  说明：                                                              │
│  - 评测流程中不会自动清理任何数据                                       │
│  - 所有测试结果和注入数据默认永久保留（留痕）                             │
│  - 仅当用户需要重新测试或释放资源时，手动执行清理                          │
│  - 清理前会打印将被删除的数据摘要，要求用户确认                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Config.yaml 完整设计

```yaml
# ============================================================
# LoCoMo 通用评测配置
# ============================================================
project_name: "LoCoMo_Eval_20260410"

# ---------- 数据集配置 ----------
data:
  locomo_json: "../data/locomo10.json"       # LoCoMo 数据文件（相对于 config.yaml）
  sample: null                                # null = 全部 sample；数字 = 指定 sample（0-based）
  sessions: null                              # null = 全部 session；"1-4" = 指定范围
  skip_category_5: true                       # 跳过 category=5（对抗性问题）

# ---------- OpenClaw 配置 ----------
openclaw:
  token: "${OPENCLAW_GATEWAY_TOKEN}"          # 支持环境变量引用
  default_agent_id: "locomo-eval"             # 默认 agent ID

  # profile 配置
  # 使用 openclaw --profile <name> 为每个场景创建完全隔离的 OpenClaw 环境
  # 每个 profile 对应独立的数据目录 ~/.openclaw-<profile_name>
  # 不同 profile 的 gateway 运行在不同端口，可并行执行
  profiles:
    openclaw_only:
      profile_name: "eval-s1"                 # openclaw --profile eval-s1
      gateway_port: 18801                     # 该 profile 的 gateway 端口
      plugins:
        context_engine: null                  # 不启用任何记忆插件
        memory: null

    openclaw_memcore:
      profile_name: "eval-s2"
      gateway_port: 18802
      plugins:
        context_engine: null
        memory: "openclaw-mem0"               # plugins.slots.memory = openclaw-mem0
      mem0_config:
        user_id_from_sample: true             # 自动从 sample_id 设置 mem0 userId

    openclaw_openviking:
      profile_name: "eval-s3"
      gateway_port: 18803
      plugins:
        context_engine: "openviking"          # plugins.slots.contextEngine = openviking
        memory: null
      openviking_plugin:
        mode: "remote"                        # local / remote
        base_url: "http://localhost:1933"
        auto_recall: true
        auto_capture: true

    openclaw_openviking_memcore:
      profile_name: "eval-s4"
      gateway_port: 18804
      plugins:
        context_engine: "openviking"
        memory: "openclaw-mem0"
      openviking_plugin:
        mode: "remote"
        base_url: "http://localhost:1933"
        auto_recall: true
        auto_capture: true
      mem0_config:
        user_id_from_sample: true

# ---------- OpenViking 配置 ----------
openviking:
  url: "http://localhost:1933"
  no_user_agent_id: true                      # 不传 user_id/agent_id

# ---------- MemCore (mem0) 配置 ----------
memcore:
  api_key: "${MEM0_API_KEY}"                  # mem0 API key

# ---------- Judge 配置 ----------
judge:
  base_url: "https://ark.cn-beijing.volces.com/api/v3"
  api_key: "${ARK_API_KEY}"
  model: "doubao-seed-2-0-pro-260215"
  temperature: 0

# ---------- 场景配置 ----------
scenarios:
  - name: "openclaw_only"
    description: "OpenClaw 纯对话，无长期记忆（基线）"
    enabled: true
    profile: "openclaw_only"                  # 对应上面 profiles 中的配置
    ingest:
      openclaw: false
      openviking: false
      memcore: false
    qa:
      user_prefix: "eval-s1"
      parallel: 10

  - name: "openclaw_memcore"
    description: "OpenClaw + MemCore(mem0)"
    enabled: true
    profile: "openclaw_memcore"
    ingest:
      openclaw: false
      openviking: false
      memcore: true
    qa:
      user_prefix: "eval-s2"
      parallel: 10

  - name: "openclaw_openviking"
    description: "OpenClaw + OpenViking 记忆"
    enabled: true
    profile: "openclaw_openviking"
    ingest:
      openclaw: true
      openviking: true
      memcore: false
    qa:
      user_prefix: "eval-s3"
      parallel: 10

  - name: "openclaw_openviking_memcore"
    description: "OpenClaw + OpenViking + MemCore"
    enabled: true
    profile: "openclaw_openviking_memcore"
    ingest:
      openclaw: true
      openviking: true
      memcore: true
    qa:
      user_prefix: "eval-s4"
      parallel: 10

# ---------- 清理配置 ----------
# 清理操作完全独立于评测流程，仅在用户手动执行 --cleanup 时触发
# 评测过程中不会自动清理任何数据，所有数据默认永久保留（留痕）
cleanup:
  require_confirmation: true                  # 清理前是否需要用户二次确认（建议始终为 true）

# ---------- 执行配置 ----------
execution:
  judge_parallel: 40                          # Judge 全局并发数
  ingest_wait_seconds: 60                     # 注入后等待秒数
  retry_count: 2                              # API 调用失败重试次数
  output_dir: "./eval_results"                # 结果输出根目录
  env_file: "~/.openviking_benchmark_env"     # 环境变量文件

# ---------- 报告配置 ----------
report:
  format: ["txt", "csv", "json"]
  compare_scenarios: true
  include_token_breakdown: true
  include_category_breakdown: true
```

---

## 5. Stage 0: 环境预检 & 部署

### 5.1 环境预检清单

脚本启动时自动执行以下检查，任何一项失败则中止并给出明确提示：

```python
class EnvironmentChecker:
    """环境预检"""

    def check_all(self):
        self.check_openclaw_installed()     # openclaw --version
        self.check_openclaw_gateway()       # curl {base_url}/health
        self.check_openviking_service()     # curl {ov_url}/health (仅含 OV 的场景)
        self.check_data_file()              # locomo10.json 是否存在
        self.check_env_vars()               # 必要的环境变量/token 是否配置
        self.check_plugins_installed()      # 所需插件是否已安装
        self.dump_openclaw_profile()        # 输出当前 profile 到日志

    def check_openclaw_installed(self):
        """检查 openclaw CLI 是否可用"""
        result = subprocess.run(["openclaw", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            raise EnvironmentError("openclaw 未安装或不在 PATH 中")
        self.openclaw_version = result.stdout.strip()
        log.info(f"OpenClaw version: {self.openclaw_version}")

    def dump_openclaw_profiles(self):
        """输出所有评测 profile 的配置状态"""
        for scenario in self.enabled_scenarios:
            profile_name = scenario.profile_name
            state_dir = os.path.expanduser(f"~/.openclaw-{profile_name}")
            if os.path.exists(state_dir):
                result = subprocess.run(
                    ["openclaw", "--profile", profile_name, "config", "get", "plugins"],
                    capture_output=True, text=True
                )
                log.info(f"Profile '{profile_name}' plugins:\n{result.stdout}")
            else:
                log.info(f"Profile '{profile_name}' not yet created (will be initialized)")

        # 保存所有 profile 状态到输出目录
        with open(f"{output_dir}/openclaw_profiles_initial.txt", "w") as f:
            f.write(profiles_summary)

    def check_plugins_installed(self):
        """检查所需插件是否已安装"""
        result = subprocess.run(
            ["openclaw", "plugins", "list"],
            capture_output=True, text=True,
            env={**os.environ, "OPENCLAW_STATE_DIR": self.state_dir}
        )
        installed_plugins = parse_plugin_list(result.stdout)

        for scenario in enabled_scenarios:
            profile = self.profiles[scenario.profile]
            if profile.plugins.context_engine == "openviking":
                assert "openviking" in installed_plugins, \
                    f"场景 {scenario.name} 需要 OpenViking 插件，请先运行: ov-install"
            if profile.plugins.memory == "openclaw-mem0":
                assert "openclaw-mem0" in installed_plugins, \
                    f"场景 {scenario.name} 需要 openclaw-mem0 插件"
```

### 5.2 OpenViking 插件部署（如未安装）

框架提供辅助命令帮助用户快速部署：

```bash
# 安装 OpenViking 插件（一键）
python run_eval.py --setup-openviking-plugin

# 内部执行:
# 1. npm install -g openclaw-openviking-setup-helper
# 2. ov-install --workdir ~/.openclaw
# 3. openclaw plugins enable openviking
# 4. openclaw config set plugins.slots.contextEngine openviking
# 5. source ~/.openclaw/openviking.env && openclaw gateway restart
```

### 5.3 OpenClaw Profile 管理（核心机制）

`openclaw --profile <name>` 是 OpenClaw 的全局 CLI 标志，其核心能力：

- 将 OpenClaw 的运行状态（配置、会话、凭证、插件等）**完全隔离**到 `~/.openclaw-<name>` 目录
- 每个 profile 拥有独立的 `openclaw.json` 配置文件
- 不同 profile 的 gateway 可以**同时运行在不同端口**上，互不干扰
- 新创建的 profile 是一个**全新干净的环境**

#### 评测框架如何利用 profile

为每个测试场景创建独立的 profile，实现完全隔离：

| 场景 | Profile 名称 | 数据目录 | Gateway 端口 |
|------|-------------|---------|-------------|
| `openclaw_only` | `eval-s1` | `~/.openclaw-eval-s1` | 18801 |
| `openclaw_memcore` | `eval-s2` | `~/.openclaw-eval-s2` | 18802 |
| `openclaw_openviking` | `eval-s3` | `~/.openclaw-eval-s3` | 18803 |
| `openclaw_openviking_memcore` | `eval-s4` | `~/.openclaw-eval-s4` | 18804 |

#### Profile 初始化流程

每个场景的 profile 会经历以下初始化步骤：

```bash
# 1. 创建全新干净的 profile（首次自动创建目录 ~/.openclaw-eval-s3）
openclaw --profile eval-s3 onboard

# 2. 设置 gateway 端口
openclaw --profile eval-s3 config set gateway.http.port 18803

# 3. 安装 OpenViking 插件（如场景需要）
ov-install --workdir ~/.openclaw-eval-s3

# 4. 启用 OpenViking 插件
openclaw --profile eval-s3 plugins enable openviking
openclaw --profile eval-s3 config set plugins.slots.contextEngine openviking
openclaw --profile eval-s3 config set plugins.entries.openviking.config.mode remote
openclaw --profile eval-s3 config set plugins.entries.openviking.config.baseUrl http://localhost:1933

# 5. 启用 mem0 插件（如场景需要）
openclaw --profile eval-s3 config set plugins.slots.memory openclaw-mem0
openclaw --profile eval-s3 config set plugins.entries.openclaw-mem0.enabled true --json

# 6. 启动 gateway
openclaw --profile eval-s3 gateway start

# 7. 验证 profile 配置
openclaw --profile eval-s3 config get plugins
```

#### ProfileManager 代码设计

```python
class ProfileManager:
    """基于 openclaw --profile 管理多个隔离环境"""

    def __init__(self, config):
        self.config = config

    def _run_openclaw(self, profile_name: str, *args) -> subprocess.CompletedProcess:
        """执行带 --profile 的 openclaw 命令"""
        cmd = ["openclaw", "--profile", profile_name, *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=True)

    def init_profile(self, scenario) -> str:
        """初始化场景的 profile，返回 gateway URL"""
        profile_name = scenario.profile_name  # e.g. "eval-s3"
        port = scenario.gateway_port          # e.g. 18803
        profile_config = self.config.openclaw.profiles[scenario.profile]

        log.info(f"[{scenario.name}] Initializing profile '{profile_name}'...")

        # 1. 检查 profile 是否已存在
        state_dir = os.path.expanduser(f"~/.openclaw-{profile_name}")
        is_new = not os.path.exists(state_dir)

        if is_new:
            # 首次初始化（onboard 创建干净环境）
            self._run_openclaw(profile_name, "onboard")
            log.info(f"  Created new profile: {state_dir}")

        # 2. 配置 gateway 端口
        self._run_openclaw(
            profile_name, "config", "set", "gateway.http.port", str(port)
        )

        # 3. 安装和配置插件
        self._setup_plugins(profile_name, profile_config)

        # 4. 启动 gateway
        self._run_openclaw(profile_name, "gateway", "start")

        # 5. 等待就绪
        gateway_url = f"http://127.0.0.1:{port}"
        self._wait_for_ready(gateway_url)

        # 6. 记录 profile 快照
        result = self._run_openclaw(profile_name, "config", "get", "plugins")
        log.info(f"  Profile plugins config:\n{result.stdout}")

        return gateway_url

    def _setup_plugins(self, profile_name: str, profile_config: dict):
        """配置场景所需的插件"""
        plugins = profile_config.get("plugins", {})

        # contextEngine 插件（OpenViking）
        context_engine = plugins.get("context_engine")
        if context_engine == "openviking":
            # 安装 OpenViking 插件
            state_dir = os.path.expanduser(f"~/.openclaw-{profile_name}")
            subprocess.run(["ov-install", "--workdir", state_dir, "-y"], check=True)
            self._run_openclaw(profile_name, "plugins", "enable", "openviking")
            self._run_openclaw(
                profile_name, "config", "set",
                "plugins.slots.contextEngine", "openviking"
            )
            # 设置 OpenViking 插件参数
            ov_config = profile_config.get("openviking_plugin", {})
            for key, value in ov_config.items():
                self._run_openclaw(
                    profile_name, "config", "set",
                    f"plugins.entries.openviking.config.{key}", str(value)
                )
        else:
            self._run_openclaw(
                profile_name, "config", "set", "plugins.slots.contextEngine", ""
            )

        # memory 插件（mem0）
        memory = plugins.get("memory")
        if memory == "openclaw-mem0":
            self._run_openclaw(
                profile_name, "config", "set", "plugins.slots.memory", "openclaw-mem0"
            )
            self._run_openclaw(
                profile_name, "config", "set",
                "plugins.entries.openclaw-mem0.enabled", "true", "--json"
            )
        else:
            self._run_openclaw(
                profile_name, "config", "set", "plugins.slots.memory", ""
            )

    def stop_profile(self, profile_name: str):
        """停止指定 profile 的 gateway"""
        self._run_openclaw(profile_name, "gateway", "stop")

    def delete_profile(self, profile_name: str):
        """删除整个 profile（用于清理）"""
        self.stop_profile(profile_name)
        state_dir = os.path.expanduser(f"~/.openclaw-{profile_name}")
        if os.path.exists(state_dir):
            shutil.rmtree(state_dir)
            log.info(f"Deleted profile: {state_dir}")

    def _wait_for_ready(self, gateway_url: str, timeout=30):
        """等待 gateway 就绪"""
        import requests
        for _ in range(timeout):
            try:
                resp = requests.get(f"{gateway_url}/health", timeout=2)
                if resp.status_code == 200:
                    log.info(f"  Gateway ready at {gateway_url}")
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError(f"Gateway not ready after {timeout}s: {gateway_url}")
```

#### Profile 带来的好处

1. **完全隔离**：每个场景的 OpenClaw 配置、session、插件互不影响
2. **可并行**：不同 profile 的 gateway 运行在不同端口，可同时执行多个场景
3. **干净环境**：新 profile 从零开始，不受之前测试的残留数据影响
4. **易清理**：删除 `~/.openclaw-<name>` 目录即可彻底清理某个场景的所有 OpenClaw 数据
5. **可复现**：profile 的配置过程完全脚本化，每次测试环境完全一致

---

## 6. 环境清理（独立操作，仅手动触发）

> **核心原则：测试数据默认永久保留，不在评测流程中自动清理。清理是完全独立的操作，
> 仅当用户明确需要时手动执行。**

### 6.1 为什么数据需要留痕

- 评测结果（CSV、JSON、summary）用于后续分析和对比
- 注入到 OpenViking / MemCore 的记忆数据可复用于多次 QA（避免重复注入）
- OpenClaw session 归档文件包含完整的 token 统计信息
- 方便排查问题（比如某个回答不对，可以回溯 session 上下文）

### 6.2 清理能力总览

| 清理目标 | 实现方式 | CLI 命令 |
|---------|---------|---------|
| **OpenClaw profile 环境** | 停止 gateway + 删除 `~/.openclaw-<profile>` 整个目录 | `--cleanup --target openclaw-profile` |
| **OpenClaw session 数据** | 删除 profile 中 `agents/{agent_id}/sessions/` 下的文件 | `--cleanup --target openclaw-sessions` |
| **OpenViking 记忆数据** | 调用 `openviking` SDK 的 `delete_session()` / `rm()` API | `--cleanup --target openviking` |
| **MemCore(mem0) 用户数据** | 复用 `mem0/delete_user.py` 的逻辑，按 sample_id 删除 | `--cleanup --target memcore` |
| **本地评测结果文件** | 删除 `eval_results/{scenario}/` 下的 CSV 等文件 | `--cleanup --target results` |
| **全部数据** | 以上全部（最彻底，相当于恢复到测试前状态） | `--cleanup --target all` |

> 由于使用了 `--profile` 机制，**最简单的清理方式**是直接删除整个 profile 目录
> （`~/.openclaw-eval-s3`），这会清除该场景的所有 OpenClaw 数据（配置、session、
> 插件、缓存等）。下次测试会自动重新创建。

### 6.3 清理模块设计

```python
class CleanupManager:
    """
    数据清理管理器。
    所有清理操作需要用户二次确认，且不在评测流程中自动调用。
    """

    def cleanup(self, scenario_name: str | None, targets: list[str]):
        """
        手动清理指定场景（或全部场景）的数据。

        Args:
            scenario_name: 指定场景名，None 表示全部场景
            targets: 要清理的目标列表 ["openclaw", "openviking", "memcore", "results", "all"]
        """
        # Step 1: 收集将被删除的数据摘要
        summary = self._collect_cleanup_summary(scenario_name, targets)

        # Step 2: 打印摘要，要求用户确认
        print("\n⚠️  以下数据将被永久删除:\n")
        for item in summary:
            print(f"  - {item}")
        print()

        confirm = input("确认删除？输入 'yes' 继续，其他输入取消: ")
        if confirm.strip().lower() != "yes":
            print("已取消清理操作。")
            return

        # Step 3: 执行清理
        if "openclaw" in targets or "all" in targets:
            self._cleanup_openclaw_sessions(scenario_name)

        if "openviking" in targets or "all" in targets:
            asyncio.run(self._cleanup_openviking_data(scenario_name))

        if "memcore" in targets or "all" in targets:
            self._cleanup_memcore_data(scenario_name)

        if "results" in targets or "all" in targets:
            self._cleanup_result_files(scenario_name)

        print("\n✅ 清理完成。")

    def _cleanup_openclaw_sessions(self, scenario_name: str | None):
        """清理 OpenClaw session 数据"""
        agents_dir = os.path.join(self.state_dir, "agents")
        if not os.path.exists(agents_dir):
            return

        for agent_name in os.listdir(agents_dir):
            sessions_dir = os.path.join(agents_dir, agent_name, "sessions")
            if not os.path.isdir(sessions_dir):
                continue

            for f in os.listdir(sessions_dir):
                if not f.endswith(".jsonl"):
                    continue
                # 如果指定了场景名，只删该场景的；否则删全部评测相关的
                if scenario_name and scenario_name not in f:
                    continue
                os.remove(os.path.join(sessions_dir, f))
                log.info(f"  Removed session: {f}")

    async def _cleanup_openviking_data(self, scenario_name: str | None):
        """清理 OpenViking 中注入的记忆数据"""
        import openviking as ov

        client = ov.AsyncHTTPClient(url=self.openviking_url)
        await client.initialize()

        try:
            # 从 import_success.csv 中读取已导入的 session 信息
            scenarios = [scenario_name] if scenario_name else self._get_all_scenarios()
            for sn in scenarios:
                import_csv = os.path.join(self.output_dir, sn, "import_success.csv")
                if not os.path.exists(import_csv):
                    continue
                with open(import_csv, "r") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        session_id = row.get("session_id")
                        if session_id:
                            await client.delete_session(session_id)
                            log.info(f"  Deleted OV session: {session_id}")
        finally:
            await client.close()

    def _cleanup_memcore_data(self, scenario_name: str | None):
        """清理 MemCore(mem0) 用户数据"""
        scenarios = [scenario_name] if scenario_name else self._get_all_scenarios()
        for sn in scenarios:
            for sample_id in self._get_ingested_sample_ids(sn):
                self._delete_mem0_user(sample_id)
                log.info(f"  Deleted mem0 user: {sample_id}")

    def _cleanup_result_files(self, scenario_name: str | None):
        """清理本地评测结果文件"""
        if scenario_name:
            target_dir = os.path.join(self.output_dir, scenario_name)
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
                log.info(f"  Removed result directory: {target_dir}")
        else:
            if os.path.exists(self.output_dir):
                shutil.rmtree(self.output_dir)
                log.info(f"  Removed all results: {self.output_dir}")
```

### 6.4 清理命令示例

```bash
# 查看将被清理的数据（dry-run，不实际删除）
python run_eval.py --cleanup --target all --dry-run

# 清理指定场景的 OpenClaw profile（最简单的方式：删除 ~/.openclaw-eval-s3）
python run_eval.py --cleanup --scenario openclaw_openviking --target openclaw-profile

# 仅清理 OpenClaw session 文件（保留 profile 配置和插件）
python run_eval.py --cleanup --scenario openclaw_openviking --target openclaw-sessions

# 清理所有场景的 OpenViking 记忆数据
python run_eval.py --cleanup --target openviking

# 清理指定场景的全部数据（profile + 记忆 + 结果文件）
python run_eval.py --cleanup --scenario openclaw_memcore --target all

# 清理全部场景的全部数据（需输入 'yes' 二次确认）
# 会删除所有 eval profile、OV 记忆、mem0 数据和本地结果
python run_eval.py --cleanup --target all
```

---

## 7. 目录结构设计

```
benchmark/locomo/
├── config.yaml                     # 用户配置文件
├── config.example.yaml             # 配置文件示例（含详细注释）
├── run_eval.py                     # 主入口脚本
├── eval_framework/                 # 框架代码
│   ├── __init__.py
│   ├── config.py                   # 配置加载 & 环境变量解析
│   ├── environment.py              # 环境预检 & OpenClaw profile 管理
│   ├── scenario_runner.py          # 场景运行器（编排各阶段）
│   ├── cleanup.py                  # 数据清理管理器
│   ├── phases/                     # 各阶段实现
│   │   ├── __init__.py
│   │   ├── ingest.py               # 注入阶段（整合 OV/OpenClaw/mem0）
│   │   ├── qa.py                   # QA 阶段
│   │   ├── judge.py                # 裁判阶段
│   │   └── stat.py                 # 统计阶段
│   └── report.py                   # 报告生成器
├── eval_results/                   # 运行结果（自动生成）
│   ├── openclaw_profile_initial.txt    # 初始 profile 快照
│   ├── environment_check.log           # 预检日志
│   ├── openclaw_only/
│   │   ├── openclaw_profile.txt        # 该场景的 profile 快照
│   │   ├── qa_results.csv
│   │   ├── import_success.csv
│   │   ├── cleanup.log
│   │   └── summary.txt
│   ├── openclaw_memcore/
│   │   ├── ...
│   ├── openclaw_openviking/
│   │   ├── ...
│   ├── openclaw_openviking_memcore/
│   │   ├── ...
│   ├── comparison_report.txt
│   └── comparison_report.json
├── openclaw/                       # 现有代码（不修改，作为依赖复用）
├── vikingbot/
└── mem0/
```

---

## 8. 主入口 `run_eval.py` CLI 设计

```
用法:
  python run_eval.py                                   # 按 config.yaml 运行全部已启用场景
  python run_eval.py --config my_config.yaml           # 指定配置文件
  python run_eval.py --scenario openclaw_only           # 只运行指定场景
  python run_eval.py --phase ingest                    # 只运行指定阶段
  python run_eval.py --phase qa,judge                  # 运行多个阶段
  python run_eval.py --skip-ingest                     # 跳过注入
  python run_eval.py --report-only                     # 仅从已有结果生成报告
  python run_eval.py --cleanup-only                    # 仅执行数据清理
  python run_eval.py --force-ingest                    # 强制重新注入
  python run_eval.py --sample 0                        # 指定特定 sample
  python run_eval.py --check-env                       # 仅执行环境预检
  python run_eval.py --setup-openviking-plugin         # 安装 OpenViking 插件
  python run_eval.py --dump-profile                    # 输出当前 OpenClaw 配置
```

### 核心参数

| 参数 | 说明 |
|------|------|
| `--config` | 配置文件路径，默认 `config.yaml` |
| `--scenario` | 只运行指定场景（可多次指定） |
| `--phase` | 只运行指定阶段：`setup,ingest,qa,judge,stat,cleanup` |
| `--skip-ingest` | 跳过注入阶段（数据已导入时使用） |
| `--report-only` | 不运行测试，仅从已有 CSV 生成对比报告 |
| `--cleanup` | 手动清理数据（独立于评测流程，需二次确认）|
| `--target` | 清理目标：openclaw / openviking / memcore / results / all |
| `--dry-run` | 仅显示将被清理的数据，不实际删除 |
| `--force-ingest` | 强制重新注入（忽略已导入记录） |
| `--sample` | 覆盖配置中的 sample 设置 |
| `--check-env` | 仅执行环境预检，不运行测试 |
| `--setup-openviking-plugin` | 辅助安装 OpenViking 插件 |
| `--dump-profile` | 输出 OpenClaw 当前 profile 配置 |

---

## 9. 核心模块详细设计

### 9.1 场景运行器 `ScenarioRunner`

```python
class ScenarioRunner:
    """负责编排单个场景的全部阶段"""

    def __init__(self, scenario_config, global_config, profile_mgr, cleanup_mgr):
        self.scenario = scenario_config
        self.config = global_config
        self.profile_mgr = profile_mgr
        self.cleanup_mgr = cleanup_mgr
        self.output_dir = f"{global_config.execution.output_dir}/{scenario_config.name}"

    def run(self, phases: list[str] = None):
        """运行场景的指定阶段（默认全部）
        注意：清理(cleanup)不在评测流程中，需用户通过 --cleanup 单独触发
        """
        all_phases = ["setup", "ingest", "qa", "judge", "stat"]
        phases = phases or all_phases

        if "setup" in phases:
            self.run_setup()
        if "ingest" in phases:
            self.run_ingest()
        if "qa" in phases:
            self.run_qa()
        if "judge" in phases:
            self.run_judge()
        if "stat" in phases:
            self.run_stat()

    def run_setup(self):
        """Stage 1: 切换 profile & 重启 gateway"""
        profile_config = self.config.openclaw.profiles[self.scenario.profile]
        self.profile_mgr.apply_profile(profile_config)

        # 保存该场景的 profile 快照
        profile_dump = self.profile_mgr.dump_current_profile()
        with open(f"{self.output_dir}/openclaw_profile.txt", "w") as f:
            f.write(profile_dump)

    def run_ingest(self):
        """Stage 2: 根据场景配置调用注入"""
        ingest_config = self.scenario.ingest

        if ingest_config.openviking:
            IngestPhase.ingest_openviking(self.config, self.scenario, self.output_dir)

        if ingest_config.openclaw:
            IngestPhase.ingest_openclaw(self.config, self.scenario, self.output_dir)

        if ingest_config.memcore:
            IngestPhase.ingest_memcore(self.config, self.scenario, self.output_dir)

        # 等待后端完成索引
        log.info(f"Waiting {self.config.execution.ingest_wait_seconds}s for indexing...")
        time.sleep(self.config.execution.ingest_wait_seconds)

    def run_qa(self):
        """Stage 3: 并发执行 QA"""
        QAPhase.run(self.config, self.scenario, self.output_dir)

    def run_judge(self):
        """Stage 4: LLM 裁判"""
        JudgePhase.run(self.config, self.scenario, self.output_dir)

    def run_stat(self):
        """Stage 5: 统计"""
        return StatPhase.run(self.config, self.scenario, self.output_dir)
```

### 9.2 场景隔离策略

| 隔离维度 | 方式 | 示例 |
|---------|------|------|
| **QA session** | session_key 带场景前缀 | `openclaw_openviking-qa-conv26-q3` |
| **QA user** | user 带场景前缀 | `eval-s3-conv26` |
| **输出文件** | 独立输出目录 | `eval_results/openclaw_openviking/` |
| **OpenClaw 插件** | 每场景切换 profile | `plugins.slots.contextEngine = openviking` |
| **OpenViking 数据** | 独立导入记录 | `eval_results/{scenario}/import_success.csv` |

### 9.3 MemCore(mem0) 特殊处理

mem0 场景的 QA 需要额外步骤：每个 sample 切换前更新 `openclaw.json` 中 mem0 的 `userId`，然后重启 gateway：

```python
class MemCoreHelper:
    """MemCore(mem0) 辅助工具"""

    def switch_mem0_user(self, sample_id: str):
        """切换 mem0 用户（改配置 + 重启 gateway）"""
        # 1. 修改 openclaw.json
        config_path = os.path.join(self.state_dir, "openclaw.json")
        with open(config_path, "r") as f:
            config = json.load(f)

        entries = config.setdefault("plugins", {}).setdefault("entries", {})
        mem0_entry = entries.setdefault("openclaw-mem0", {})
        mem0_entry["enabled"] = True
        mem0_entry.setdefault("config", {})["userId"] = sample_id

        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # 2. 重启 gateway
        self.profile_mgr.restart_gateway()
```

---

## 10. 报告设计

### 10.1 单场景报告 (`summary.txt`)

```
=== LoCoMo Eval Report: openclaw_openviking ===
Description: OpenClaw + OpenViking 记忆
Run Time: 2026-04-10 15:30:00
OpenClaw Profile: [附完整 profile 快照]
Data: locomo10.json (10 samples, sessions 1-4)

--- Accuracy ---
Total Questions: 87 (excluding category=5)
Correct: 62
Wrong: 25
Accuracy: 71.26%

--- Accuracy by Category ---
Category 1 (单跳事实): 80.00% (20/25)
Category 2 (多跳推理): 68.18% (15/22)
Category 3 (时间相关): 65.00% (13/20)
Category 4 (开放式):   70.00% (14/20)

--- QA Token Usage ---
Total Input Tokens: 1,234,567
Total Output Tokens: 45,678
Total Cache Read: 234,567
Avg Input per Question: 14,190.4
Avg Output per Question: 525.0

--- Ingest Token Usage (OpenViking) ---
Total Embedding Tokens: 123,456
Total VLM Tokens: 78,901
Total Ingest Tokens: 202,357

--- Grand Total ---
Total Tokens Consumed: 1,482,245
```

### 10.2 跨场景对比报告 (`comparison_report.txt`)

```
============================================================
  LoCoMo 多场景对比报告
  Project: LoCoMo_Eval_20260410
  Generated: 2026-04-10 16:00:00
  Data: locomo10.json | 10 samples | 87 questions
============================================================

=== 总体准确率对比 ===

┌─────────────────────────────┬───────────┬──────────┬────────────┐
│ 场景                         │ 准确率     │ 正确/总计 │ 较基线提升   │
├─────────────────────────────┼───────────┼──────────┼────────────┤
│ openclaw_only (基线)         │ 45.98%    │ 40/87    │ -          │
│ openclaw_memcore            │ 63.22%    │ 55/87    │ +17.24%    │
│ openclaw_openviking         │ 71.26%    │ 62/87    │ +25.28%    │
│ openclaw_openviking_memcore │ 74.71%    │ 65/87    │ +28.73%    │
└─────────────────────────────┴───────────┴──────────┴────────────┘

=== 按 Category 分类准确率 ===

Category 1 (单跳事实):
  openclaw_only:                52.00% (13/25)
  openclaw_memcore:             72.00% (18/25)
  openclaw_openviking:          80.00% (20/25)
  openclaw_openviking_memcore:  84.00% (21/25)

Category 2 (多跳推理):
  openclaw_only:                40.91% (9/22)
  openclaw_memcore:             54.55% (12/22)
  openclaw_openviking:          68.18% (15/22)
  openclaw_openviking_memcore:  72.73% (16/22)

Category 3 (时间相关):
  openclaw_only:                35.00% (7/20)
  openclaw_memcore:             55.00% (11/20)
  openclaw_openviking:          65.00% (13/20)
  openclaw_openviking_memcore:  65.00% (13/20)

Category 4 (开放式):
  openclaw_only:                55.00% (11/20)
  openclaw_memcore:             70.00% (14/20)
  openclaw_openviking:          70.00% (14/20)
  openclaw_openviking_memcore:  75.00% (15/20)

=== Token 成本对比 ===

┌─────────────────────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ 场景                         │ QA Input     │ QA Output    │ Ingest       │ 总 Tokens    │
├─────────────────────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ openclaw_only               │ 204,435      │ 27,180       │ 0            │ 231,615      │
│ openclaw_memcore            │ 735,603      │ 39,723       │ 0            │ 775,326      │
│ openclaw_openviking         │ 1,234,567    │ 45,678       │ 202,357      │ 1,482,602    │
│ openclaw_openviking_memcore │ 1,345,678    │ 46,456       │ 202,357      │ 1,594,491    │
└─────────────────────────────┴──────────────┴──────────────┴──────────────┴──────────────┘

=== 性价比分析 ===

┌─────────────────────────────┬───────────┬──────────────┬────────────────────┐
│ 场景                         │ 准确率     │ 总 Tokens    │ 每 1% 准确率 Token  │
├─────────────────────────────┼───────────┼──────────────┼────────────────────┤
│ openclaw_only               │ 45.98%    │ 231,615      │ 5,037              │
│ openclaw_memcore            │ 63.22%    │ 775,326      │ 12,265             │
│ openclaw_openviking         │ 71.26%    │ 1,482,602    │ 20,807             │
│ openclaw_openviking_memcore │ 74.71%    │ 1,594,491    │ 21,342             │
└─────────────────────────────┴───────────┴──────────────┴────────────────────┘
```

### 10.3 JSON 格式报告

```json
{
  "project_name": "LoCoMo_Eval_20260410",
  "run_time": "2026-04-10T16:00:00",
  "openclaw_version": "1.2.3",
  "data": {
    "locomo_json": "locomo10.json",
    "total_samples": 10,
    "total_questions": 87
  },
  "scenarios": [
    {
      "name": "openclaw_only",
      "description": "OpenClaw 纯对话，无长期记忆（基线）",
      "openclaw_profile": "...",
      "accuracy": 0.4598,
      "correct": 40,
      "total": 87,
      "improvement_over_baseline": null,
      "token_usage": {
        "qa": { "input": 204435, "output": 27180, "cache_read": 0, "total": 231615 },
        "ingest": { "embedding": 0, "vlm": 0, "total": 0 },
        "grand_total": 231615
      },
      "category_accuracy": {
        "1": { "correct": 13, "total": 25, "accuracy": 0.52 },
        "2": { "correct": 9, "total": 22, "accuracy": 0.4091 },
        "3": { "correct": 7, "total": 20, "accuracy": 0.35 },
        "4": { "correct": 11, "total": 20, "accuracy": 0.55 }
      }
    }
  ]
}
```

---

## 11. 完整使用步骤

### Step 1: 环境准备

```bash
# 1.1 确保 OpenClaw 已安装
openclaw --version

# 1.2 确保 OpenViking 服务已启动
curl http://localhost:1933/health

# 1.3 准备 LoCoMo 数据
# 确保 benchmark/locomo/data/locomo10.json 存在

# 1.4（可选）手动测试 profile 机制
# 创建一个临时 profile 验证环境
openclaw --profile eval-test onboard
openclaw --profile eval-test config get gateway
# 确认目录 ~/.openclaw-eval-test 已创建
```

### Step 2: 配置

```bash
# 2.1 复制配置模板
cp config.example.yaml config.yaml

# 2.2 编辑配置
# - 填写服务地址、token
# - 选择要启用的场景
# - 配置 judge API key

# 2.3 设置环境变量
export OPENCLAW_GATEWAY_TOKEN="your_token"
export ARK_API_KEY="your_judge_api_key"
export MEM0_API_KEY="your_mem0_key"          # 如需 mem0 场景
```

### Step 3: 环境预检

```bash
# 检查所有配置是否正确（OpenClaw 可用、OV 服务在线、数据文件存在等）
python run_eval.py --check-env

# 输出所有评测 profile 的配置（如已存在）
python run_eval.py --dump-profile

# 首次运行时，脚本会自动为每个场景:
# 1. 通过 openclaw --profile eval-sN onboard 创建干净环境
# 2. 安装所需插件（OpenViking / mem0）
# 3. 配置插件参数
# 4. 在独立端口启动 gateway
```

### Step 4: 运行测试

```bash
# 方式 A: 一键运行全部场景
python run_eval.py

# 方式 B: 分步运行
python run_eval.py --scenario openclaw_only          # 先跑基线
python run_eval.py --scenario openclaw_openviking    # 再跑 OV 场景

# 方式 C: 分阶段运行（调试用）
python run_eval.py --scenario openclaw_openviking --phase setup,ingest  # 先导入
python run_eval.py --scenario openclaw_openviking --phase qa            # 再 QA
python run_eval.py --scenario openclaw_openviking --phase judge,stat    # 最后打分统计
```

### Step 5: 查看结果

```bash
# 查看单场景结果
cat eval_results/openclaw_openviking/summary.txt

# 查看对比报告
cat eval_results/comparison_report.txt

# JSON 结构化数据（供进一步分析）
cat eval_results/comparison_report.json
```

### Step 6: 清理数据（可选，仅手动触发）

> 评测流程不会自动清理数据。所有注入的记忆和测试结果默认保留。
> 仅当你需要重新测试或释放资源时，手动执行以下清理命令。

```bash
# 查看将被清理的数据（不实际删除）
python run_eval.py --cleanup --target all --dry-run

# 清理指定场景的 OpenClaw session
python run_eval.py --cleanup --scenario openclaw_openviking --target openclaw

# 清理全部 OpenViking 记忆数据
python run_eval.py --cleanup --target openviking

# 清理全部数据（需输入 'yes' 二次确认）
python run_eval.py --cleanup --target all
```

---

## 12. 与现有代码的关系

| 现有模块 | 复用方式 | 是否修改 |
|---------|---------|---------|
| `openclaw/eval.py` | import 复用 `send_message`、`load_locomo_data`、`build_session_messages` 等 | 不修改 |
| `openclaw/import_to_ov.py` | import 复用 `viking_ingest`、`build_session_messages` | 不修改 |
| `openclaw/judge.py` | import 复用 `grade_answer` | 不修改 |
| `openclaw/stat_judge_result.py` | 参考逻辑，在 `stat.py` 中扩展实现 | 不修改 |
| `mem0/ingest.py` | import 复用 mem0 注入逻辑 | 不修改 |
| `mem0/eval.py` | import 复用 `_update_openclaw_mem0_user` | 不修改 |
| `mem0/delete_user.py` | import 复用删除逻辑 | 不修改 |

---

## 13. 技术依赖

```
# requirements.txt
requests>=2.31.0
openai>=1.0.0
openviking>=0.1.0
python-dotenv>=1.0.0
pyyaml>=6.0
tabulate>=0.9.0          # 表格格式报告
```

---

## 14. 实现计划

| 阶段 | 内容 | 预估工作量 |
|------|------|-----------|
| P1 | `config.py` 配置加载 + 环境变量解析 | 0.5 天 |
| P2 | `environment.py` 环境预检 + Profile 管理 + OpenClaw CLI 封装 | 1 天 |
| P3 | `phases/ingest.py` 统一注入（OV + OpenClaw + mem0） | 1 天 |
| P4 | `phases/qa.py` QA 阶段（带场景隔离） | 0.5 天 |
| P5 | `phases/judge.py` 裁判阶段 | 0.5 天 |
| P6 | `phases/stat.py` 统计 + `report.py` 报告生成（含对比） | 1 天 |
| P7 | `cleanup.py` 数据清理（OC + OV + mem0） | 0.5 天 |
| P8 | `run_eval.py` 主入口 + CLI | 0.5 天 |
| P9 | `config.example.yaml` + 文档 | 0.5 天 |
| P10 | 集成测试 & 边界情况处理 | 1 天 |
| **合计** | | **7 天** |

---

## 15. 后续扩展

- **更多评测数据集**: 框架可扩展支持其他数据集格式（FinanceBench、QASPER 等）
- **更多记忆后端**: `ingest.py` 可通过策略模式扩展新的记忆后端
- **自动化 CI**: JSON 报告可接入 CI，自动对比 PR 前后的准确率变化
- **可视化 Dashboard**: JSON 报告可对接前端展示趋势图表
- **多 OpenClaw 实例并行**: 支持同时启动多个 OpenClaw 实例（不同端口 + `OPENCLAW_STATE_DIR`），实现场景并行
