"""
OpenClaw Plugin 测试框架 — 全局 Fixtures & 配置
"""

import logging

import pytest

from config.settings import get_effective_config

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def _log_effective_config():
    """Log the effective configuration at session start."""
    cfg = get_effective_config()
    logger.info("=" * 60)
    logger.info("Effective test configuration:")
    logger.info("  Plugin: %s @ %s", cfg["versions"]["plugin_package"], cfg["versions"]["plugin"])
    logger.info("  OpenViking: %s", cfg["versions"]["openviking"])
    logger.info("  OV port: %s | OC gateway port: %s", cfg["ports"]["openviking"], cfg["ports"]["openclaw_gateway"])
    logger.info("  Profile: %s (port %s)", cfg["profile"]["name"], cfg["profile"]["gateway_port"])
    logger.info("  Model: %s", cfg["models"]["primary"])
    logger.info("  ov.conf candidates: %s", cfg["paths"]["ov_conf_candidates"])
    logger.info("=" * 60)
