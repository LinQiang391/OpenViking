# OpenClaw Plugin Test Framework

OpenClaw OpenViking 插件自动化测试框架，验证插件安装、交互式配置、记忆存储和跨会话召回的端到端流程。

## 环境要求

- Python >= 3.9
- Node.js >= 22.12（OpenClaw 运行时依赖）
- OpenClaw CLI 已安装（`openclaw` 命令可用）
- OpenViking Python 包已安装（系统或独立 Python 环境）
- 有效的 `ov.conf` 配置文件（含 embedding 和 VLM API key）

## 快速开始

```bash
cd openclaw-plugin

# 1. 创建虚拟环境并安装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 运行测试
rm -rf ~/.openclaw-e2e_test/    # 清理旧的 profile（如有）
python -m pytest tests/clawhub/test_local_install.py -v -s --timeout=900
```

## 目录结构

```
openclaw-plugin/
├── pyproject.toml           # pytest 配置
├── requirements.txt         # Python 依赖
├── test_config.json         # 测试参数（版本、模型、端口等）
├── conftest.py              # 全局 fixture
├── config/
│   └── settings.py          # 配置加载（test_config.json + 环境变量）
├── utils/
│   ├── profile_manager.py   # OpenClaw profile 生命周期管理
│   ├── process_manager.py   # 进程/端口工具
│   └── config_manager.py    # ov.conf 管理
└── tests/
    └── clawhub/             # clawhub 安装场景
        └── test_local_install.py
```

## 测试场景

### `tests/clawhub/` — 通过 clawhub 安装插件

| 测试文件 | 描述 |
|---------|------|
| `test_local_install.py` | Local 模式: clawhub 安装 → 交互式配置 → ingest → compact → QA → judge |

### 测试流程（固定管线）

每个安装场景遵循统一的验证管线：

1. **Setup** — 创建隔离 profile，安装插件，运行交互式配置
2. **Ingest** — 注入测试对话（多条消息，单一 session）
3. **Compact** — 通过 WebSocket RPC 端到端触发 `sessions.compact`
4. **Memory Wait** — 等待 OpenViking 生成记忆文件
5. **QA** — 在独立 session 中提问（验证跨会话记忆召回）
6. **Judge** — LLM 评判回答正确性
7. **Verify** — 文件系统 + API + Session JSONL 三重验证
8. **Teardown** — 销毁 profile，清理进程

## 配置

### `test_config.json`

```json
{
  "versions": {
    "plugin": "2026.4.30",           // 要测试的插件版本
    "plugin_package": "@openclaw/openviking"
  },
  "models": {
    "primary": "volcengine/doubao-seed-2-0-code-preview-260215",
    "judge": "volcengine/doubao-seed-2-0-code-preview-260215"
  },
  "profile": {
    "name": "e2e_test",              // 隔离 profile 名称
    "gateway_port": 19201
  },
  "judge": {
    "api_key_from_ov_conf": true     // 从 ov.conf 读取 Judge API Key
  }
}
```

### 环境变量覆盖

所有配置项均可通过环境变量覆盖：

| 变量 | 说明 |
|-----|------|
| `PLUGIN_VERSION` | 插件版本 |
| `MODEL_PRIMARY` | 主模型 |
| `PROFILE_GATEWAY_PORT` | 测试 Gateway 端口 |
| `NODE_BIN` | Node.js bin 目录路径 |
| `OV_CONF` | 指定 ov.conf 路径 |
| `OPENVIKING_PYTHON` | OpenViking 使用的 Python 路径 |

## 隔离机制

测试通过 `OPENCLAW_STATE_DIR` 环境变量创建完全隔离的 OpenClaw 环境：

- Profile 目录: `~/.openclaw-{profile_name}/`
- 独立的 `openclaw.json`、`ov.conf`、extensions、sessions
- 独立的 Gateway 端口
- 测试结束后整个目录被删除

## 添加新测试场景

1. 在 `tests/` 下创建对应的目录（如 `tests/installer/`）
2. 编写测试类，继承固定的 ingest → QA → judge 管线
3. 仅 setup 阶段（安装/配置方式）是变量

```python
@pytest.mark.local
class TestMyScenario:
    @classmethod
    def setup_class(cls):
        cls.profile = ProfileManager()
        steps = cls.profile.full_setup(mode="local")
        # 验证 setup 步骤...

    @classmethod
    def teardown_class(cls):
        cls.profile.full_teardown()

    def test_e2e_flow(self):
        # 调用统一的 ingest → compact → QA → judge → verify 管线
        ...
```
