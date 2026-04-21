"""
自动化测试框架配置

优先级: 环境变量 > test_config.json > 默认值
配置文件路径可通过 TEST_CONFIG 环境变量指定。
"""

import json
import os
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.environ.get("PROJECT_DIR", os.path.abspath(os.path.join(BASE_DIR, "..", "..")))


# ── 加载 test_config.json ────────────────────────────────────

def _load_test_config() -> Dict[str, Any]:
    config_path = os.environ.get("TEST_CONFIG", os.path.join(BASE_DIR, "test_config.json"))
    if os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


_CFG = _load_test_config()
_VERSIONS = _CFG.get("versions", {})
_PATHS = _CFG.get("paths", {})
_PORTS = _CFG.get("ports", {})
_TIMEOUTS = _CFG.get("timeouts", {})
_OPTIONS = _CFG.get("test_options", {})
_PROFILE = _CFG.get("profile", {})
_MODELS = _CFG.get("models", {})
_JUDGE = _CFG.get("judge", {})
_TEST_DATA = _CFG.get("test_data", {})
_OV_CONF = _CFG.get("ov_conf", {})


def _cfg_str(section: Dict, key: str, env_key: str, default: str) -> str:
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    cfg_val = section.get(key, "")
    if cfg_val:
        return str(cfg_val)
    return default


def _cfg_int(section: Dict, key: str, env_key: str, default: int) -> int:
    env_val = os.environ.get(env_key)
    if env_val:
        return int(env_val)
    cfg_val = section.get(key, 0)
    if cfg_val:
        return int(cfg_val)
    return default


# ── Profile（隔离的测试环境） ─────────────────────────────────

PROFILE_NAME = _cfg_str(_PROFILE, "name", "TEST_PROFILE", "e2e_test")
PROFILE_GATEWAY_PORT = _cfg_int(_PROFILE, "gateway_port", "PROFILE_GATEWAY_PORT", 19201)
PROFILE_GATEWAY_TOKEN = _cfg_str(_PROFILE, "gateway_token", "PROFILE_GATEWAY_TOKEN", "e2e-test-token-auto")
PROFILE_HOME = os.path.expanduser(f"~/.openclaw-{PROFILE_NAME}")
PROFILE_GATEWAY_URL = f"http://127.0.0.1:{PROFILE_GATEWAY_PORT}"

# Node.js path (openclaw requires Node >=22.12)
def _default_node_path() -> str:
    import shutil
    node_exe = shutil.which("node")
    if node_exe:
        return os.path.dirname(os.path.realpath(node_exe))
    return os.path.expanduser("~/.nvm/versions/node/v22.22.2/bin")

NODE_PATH = _cfg_str(_PATHS, "node_bin", "NODE_BIN", _default_node_path())


# ── OpenClaw ──────────────────────────────────────────────────

OPENCLAW_HOME = os.path.expanduser(
    _cfg_str(_PATHS, "openclaw_home", "OPENCLAW_HOME", "~/.openclaw")
)
OPENCLAW_GATEWAY_PORT = _cfg_int(_PORTS, "openclaw_gateway", "OPENCLAW_GATEWAY_PORT", 18789)
OPENCLAW_GATEWAY_URL = f"http://127.0.0.1:{OPENCLAW_GATEWAY_PORT}"


# ── OpenViking ────────────────────────────────────────────────

OPENVIKING_HOME = os.path.expanduser(
    _cfg_str(_PATHS, "openviking_home", "OPENVIKING_HOME", "~/.openviking")
)

_ov_conf_from_cfg = _cfg_str(_PATHS, "ov_conf", "OV_CONF", "")
if _ov_conf_from_cfg:
    OPENVIKING_CONF_CANDIDATES = [_ov_conf_from_cfg]
else:
    OPENVIKING_CONF_CANDIDATES = [
        os.path.join(PROJECT_ROOT, "ov.conf.temp"),
        os.path.join(PROJECT_ROOT, "ov.conf"),
        os.path.join(BASE_DIR, "ov.conf"),
        os.path.join(OPENVIKING_HOME, "ov.conf"),
        os.path.join(PROJECT_ROOT, "examples", "ov.conf.example"),
    ]

OPENVIKING_VENV = os.environ.get("OPENVIKING_VENV", os.path.join(OPENVIKING_HOME, "venv"))


def _detect_ov_port() -> int:
    port_override = _cfg_int(_PORTS, "openviking", "OPENVIKING_PORT", 0)
    if port_override:
        return port_override
    for path in OPENVIKING_CONF_CANDIDATES:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = json.load(f)
                port = cfg.get("server", {}).get("port")
                if port:
                    return int(port)
            except Exception:
                pass
    return 1933


OPENVIKING_PORT = _detect_ov_port()
OPENVIKING_URL = f"http://127.0.0.1:{OPENVIKING_PORT}"


# ── 版本 ──────────────────────────────────────────────────────

OPENVIKING_VERSION = _cfg_str(_VERSIONS, "openviking", "OPENVIKING_VERSION", "")
PLUGIN_PACKAGE = _cfg_str(_VERSIONS, "plugin_package", "PLUGIN_PACKAGE", "@openclaw/openviking")
PLUGIN_VERSION = _cfg_str(_VERSIONS, "plugin", "PLUGIN_VERSION", "2026.4.23-dev.1")
PLUGIN_INSTALL_SPEC = f"clawhub:{PLUGIN_PACKAGE}@{PLUGIN_VERSION}"
PLUGIN_UPGRADE_VERSION = _cfg_str(_VERSIONS, "plugin_upgrade_version", "PLUGIN_UPGRADE_VERSION", "")
PLUGIN_ID = "openviking"
PLUGIN_DIR_IN_REPO = os.path.join(PROJECT_ROOT, "examples", "openclaw-plugin")
PLUGIN_EXTENSIONS_DIR = os.path.join(OPENCLAW_HOME, "extensions")

_project_root_override = _cfg_str(_PATHS, "project_root", "PROJECT_DIR", "")
if _project_root_override:
    PROJECT_ROOT = _project_root_override


# ── 模型配置 ─────────────────────────────────────────────────

MODEL_PRIMARY = _cfg_str(_MODELS, "primary", "MODEL_PRIMARY", "zai/glm-4.7-flash")
MODEL_JUDGE = _cfg_str(_MODELS, "judge", "MODEL_JUDGE", "zai/glm-4.7-flash")
MODEL_PROVIDER = _MODELS.get("provider", {})

JUDGE_ENABLED = _JUDGE.get("enabled", True)
JUDGE_MODEL = _cfg_str(_JUDGE, "model", "JUDGE_MODEL", "") or MODEL_JUDGE
JUDGE_BASE_URL = _cfg_str(_JUDGE, "base_url", "JUDGE_BASE_URL", "")
JUDGE_API_KEY_ENV = _JUDGE.get("api_key_env", "VOLCENGINE_API_KEY")

# Judge API key: 优先配置文件 > ov.conf > 环境变量
_judge_api_key = _JUDGE.get("api_key", "")
if not _judge_api_key and _JUDGE.get("api_key_from_ov_conf"):
    for _ov_candidate in OPENVIKING_CONF_CANDIDATES:
        if os.path.isfile(_ov_candidate):
            try:
                with open(_ov_candidate, encoding="utf-8") as _f:
                    _ov = json.load(_f)
                _judge_api_key = _ov.get("vlm", {}).get("api_key", "") or _ov.get("api_key", "")
                if _judge_api_key:
                    break
            except Exception:
                pass
if not _judge_api_key:
    _judge_api_key = os.environ.get(JUDGE_API_KEY_ENV, "")
JUDGE_API_KEY = _judge_api_key


# ── 测试数据 ─────────────────────────────────────────────────

TEST_SESSION_MESSAGES: List[str] = _TEST_DATA.get("session_messages", [])
TEST_QA_PAIRS: List[Dict[str, Any]] = _TEST_DATA.get("qa_pairs", [])


# ── 超时 & 重试 ──────────────────────────────────────────────

COMMAND_TIMEOUT = _cfg_int(_TIMEOUTS, "command", "COMMAND_TIMEOUT", 300)
GATEWAY_START_TIMEOUT = _cfg_int(_TIMEOUTS, "gateway_start", "GATEWAY_START_TIMEOUT", 30)
OV_SERVER_START_TIMEOUT = _cfg_int(_TIMEOUTS, "ov_server_start", "OV_SERVER_START_TIMEOUT", 60)
HEALTH_CHECK_TIMEOUT = _cfg_int(_TIMEOUTS, "health_check", "HEALTH_CHECK_TIMEOUT", 30)
HEALTH_CHECK_INTERVAL = 3
MAX_RETRIES = _cfg_int(_TIMEOUTS, "max_retries", "MAX_RETRIES", 3)
RETRY_DELAY = _cfg_int(_TIMEOUTS, "retry_delay", "RETRY_DELAY", 5)
MEMORY_SYNC_WAIT = _cfg_int(_TIMEOUTS, "memory_sync_wait", "MEMORY_SYNC_WAIT", 15)
MESSAGE_TIMEOUT = _cfg_int(_TIMEOUTS, "message_timeout", "MESSAGE_TIMEOUT", 120)


# ── 测试选项 ─────────────────────────────────────────────────

SKIP_INSTALL = _OPTIONS.get("skip_install", False) or os.environ.get("SKIP_INSTALL", "") == "1"
SKIP_CLEANUP = _OPTIONS.get("skip_cleanup", False) or os.environ.get("SKIP_CLEANUP", "") == "1"
KEEP_SERVICES = _OPTIONS.get("keep_services_running", False) or os.environ.get("KEEP_SERVICES", "") == "1"


# ── 输出 ──────────────────────────────────────────────────────

LOG_DIR = os.path.join(BASE_DIR, "logs")
REPORT_DIR = os.path.join(BASE_DIR, "reports")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ── 便捷方法 ─────────────────────────────────────────────────

def get_effective_config() -> Dict[str, Any]:
    return {
        "versions": {
            "openviking": OPENVIKING_VERSION or "(auto-detect)",
            "plugin": PLUGIN_VERSION,
            "plugin_package": PLUGIN_PACKAGE,
            "plugin_install_spec": PLUGIN_INSTALL_SPEC,
        },
        "profile": {
            "name": PROFILE_NAME,
            "home": PROFILE_HOME,
            "gateway_port": PROFILE_GATEWAY_PORT,
        },
        "models": {
            "primary": MODEL_PRIMARY,
            "judge": MODEL_JUDGE,
        },
        "paths": {
            "openclaw_home": OPENCLAW_HOME,
            "openviking_home": OPENVIKING_HOME,
            "ov_conf_candidates": OPENVIKING_CONF_CANDIDATES,
            "project_root": PROJECT_ROOT,
            "node_path": NODE_PATH,
        },
        "ports": {
            "openclaw_gateway": OPENCLAW_GATEWAY_PORT,
            "openviking": OPENVIKING_PORT,
            "profile_gateway": PROFILE_GATEWAY_PORT,
        },
    }


# ── ov.conf 模板（从 test_config.json 的 ov_conf 段读取）───────
OV_CONF_TEMPLATE: Dict[str, Any] = _OV_CONF
