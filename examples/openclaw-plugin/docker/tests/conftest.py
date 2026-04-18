"""
E2E 测试 conftest — 加载配置，提供 fixtures。

环境变量覆盖（优先级高于 JSON）：
  OV_HOST / OV_PORT / OV_ROOT_API_KEY
  OC_HOST / OC_PORT / OC_GATEWAY_TOKEN
  OG_HOST / OG_PORT / OG_USER / OG_DB_NAME / OG_PASSWORD
  JUDGE_API_KEY / JUDGE_BASE_URL / JUDGE_MODEL
"""

import json
import logging
import os
import sys
from pathlib import Path

import pytest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("e2e")

CONFIG_PATH = os.environ.get(
    "E2E_CONFIG", str(Path(__file__).with_name("test_config.json"))
)


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


CFG = _load_config()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _detect_gateway_token() -> str:
    """Auto-detect OpenClaw gateway token (container first, then host fallback)."""
    import subprocess
    for cmd in (
        ["docker", "exec", "openclaw", "cat", "/root/.openclaw/openclaw.json"],
        ["sg", "docker", "-c", "docker exec openclaw cat /root/.openclaw/openclaw.json"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                oc_cfg = json.loads(result.stdout)
                token = oc_cfg.get("gateway", {}).get("auth", {}).get("token", "")
                if token:
                    logger.info("auto-detected gateway token from container")
                    return token
        except Exception:
            continue
    for candidate in (
        os.path.expanduser("~/.openclaw/openclaw.json"),
        "/root/.openclaw/openclaw.json",
    ):
        if os.path.isfile(candidate):
            try:
                with open(candidate) as f:
                    oc_cfg = json.load(f)
                token = oc_cfg.get("gateway", {}).get("auth", {}).get("token", "")
                if token:
                    logger.info("auto-detected gateway token from %s", candidate)
                    return token
            except Exception:
                pass
    return ""


class ServiceEndpoints:
    """Resolved service addresses with env-var overrides."""

    def __init__(self, cfg: dict):
        ov = cfg["services"]["openviking"]
        oc = cfg["services"]["openclaw"]
        og = cfg["services"]["opengauss"]

        self.ov_host = _env("OV_HOST", ov["host"])
        self.ov_port = int(_env("OV_PORT", str(ov["port"])))
        self.ov_root_api_key = _env("OV_ROOT_API_KEY", ov.get("root_api_key", ""))
        self.ov_account = ov.get("account_id", "default")
        self.ov_user = ov.get("user_id", "default")
        self.ov_base_url = f"http://{self.ov_host}:{self.ov_port}"

        self.oc_host = _env("OC_HOST", oc["host"])
        self.oc_port = int(_env("OC_PORT", str(oc["port"])))
        explicit_token = _env("OC_GATEWAY_TOKEN", oc.get("gateway_token", ""))
        self.oc_token = explicit_token or _detect_gateway_token()
        self.oc_base_url = f"http://{self.oc_host}:{self.oc_port}"

        self.og_host = _env("OG_HOST", og["host"])
        self.og_port = int(_env("OG_PORT", str(og["port"])))
        self.og_user = _env("OG_USER", og.get("user", "gaussdb"))
        self.og_db = _env("OG_DB_NAME", og.get("db_name", "omm"))
        self.og_password = _env("OG_PASSWORD", "")

    def ov_headers(self) -> dict:
        if self.ov_root_api_key:
            return {
                "X-API-Key": self.ov_root_api_key,
                "X-OpenViking-Account": self.ov_account,
                "X-OpenViking-User": self.ov_user,
            }
        return {}


ENDPOINTS = ServiceEndpoints(CFG)


def _detect_judge_api_key() -> str:
    """Try to auto-detect the volcengine API key for judge from the container."""
    judge_cfg = CFG.get("judge", {})
    key = os.environ.get("JUDGE_API_KEY", "")
    if key:
        return key
    if judge_cfg.get("api_key_env"):
        key = os.environ.get(judge_cfg["api_key_env"], "")
    if key:
        return key
    import subprocess
    auth_path = "/root/.openclaw/agents/main/agent/auth-profiles.json"
    for cmd in (
        ["docker", "exec", "openclaw", "cat", auth_path],
        ["sg", "docker", "-c", f"docker exec openclaw cat {auth_path}"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                profiles = json.loads(result.stdout)
                for _, v in profiles.get("profiles", {}).items():
                    if v.get("key"):
                        logger.info("auto-detected judge API key from container auth-profiles")
                        return v["key"]
        except Exception:
            continue
    return ""


JUDGE_API_KEY = _detect_judge_api_key()
if JUDGE_API_KEY and "JUDGE_API_KEY" not in os.environ:
    os.environ["JUDGE_API_KEY"] = JUDGE_API_KEY


@pytest.fixture(scope="session")
def cfg():
    return CFG


@pytest.fixture(scope="session")
def endpoints():
    return ENDPOINTS


@pytest.fixture(scope="session")
def test_data():
    return CFG["test_data"]


@pytest.fixture(scope="session")
def timeouts():
    return CFG["timeouts"]
