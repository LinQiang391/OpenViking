"""
Local 模式 E2E 测试 — clawhub 安装场景

测试流程:
  1. Setup: clawhub 安装插件 → 交互式 setup (local 模式)
  2. Pipeline: ingest → compact → QA → judge → verify
  3. Teardown: 销毁 profile
"""

import json
import logging
import uuid

import pytest

from config.settings import (
    MODEL_PRIMARY,
    PLUGIN_VERSION,
    PROFILE_GATEWAY_PORT,
    PROFILE_NAME,
    get_effective_config,
)
from utils.profile_manager import ProfileManager

from .._pipeline import (
    log_summary,
    run_ingest,
    run_judge,
    run_qa,
    trigger_compact,
    verify_ov_storage,
    wait_for_memory_files,
)

logger = logging.getLogger(__name__)


@pytest.mark.clawhub
@pytest.mark.local
class TestLocalE2ESingle:
    """Local 模式 E2E: clawhub 安装 → 交互式配置 → ingest → QA → judge → verify。"""

    SESSION_ID = f"e2e-local-{uuid.uuid4().hex[:8]}"
    profile: ProfileManager = None

    @classmethod
    def setup_class(cls):
        logger.info("=" * 60)
        logger.info("SETUP: local mode profile '%s'", PROFILE_NAME)
        cfg = get_effective_config()
        logger.info("config: %s", json.dumps(cfg, indent=2, ensure_ascii=False))
        logger.info("=" * 60)

        cls.profile = ProfileManager()
        steps = cls.profile.full_setup(mode="local")

        logger.info("setup steps: %s", steps)
        for key in ("create", "plugin_installed", "ov_conf_created",
                    "interactive_setup", "auth_copied", "models_configured",
                    "gateway_configured", "gateway_started", "ov_plugin_ready"):
            assert steps.get(key), f"setup '{key}' failed: {steps}"

        ov_port = getattr(cls.profile, "_ov_port", None)
        data_dir = getattr(cls.profile, "_ov_conf", "")
        logger.info("setup OK (OV port=%s, data=%s/)", ov_port,
                     cls.profile.home + "/openviking-data")

    @classmethod
    def teardown_class(cls):
        logger.info("TEARDOWN: destroying profile '%s'", PROFILE_NAME)
        if cls.profile:
            result = cls.profile.full_teardown()
            logger.info("teardown: %s", result)

    def test_e2e_flow(self):
        """完整 E2E 管线: ingest → sessions.compact → QA(独立session) → judge → verify。"""
        profile = self.__class__.profile
        sid = self.SESSION_ID
        user = "e2e-user"
        agent_id = "e2e-local"

        # Phase 1: Ingest
        ingest_responses = run_ingest(profile, sid, user=user, agent_id=agent_id)
        assert len(ingest_responses) > 0

        # Phase 1.5: Compact (WebSocket RPC)
        ingest_session_key = f"agent:{agent_id}:openresponses-user:{user}"
        compact_result = trigger_compact(profile, ingest_session_key)
        assert compact_result.get("success"), (
            f"sessions.compact 失败: {compact_result.get('error', compact_result)}"
        )

        # Phase 1.6: Wait for memory files
        mem_ready = wait_for_memory_files(
            profile, max_wait=180, poll_interval=10,
            expected_entities=["小明", "咪咪"],
        )
        assert mem_ready, "OpenViking 未在 180s 内生成预期的记忆文件"

        # Phase 2: QA (独立 session)
        qa_results = run_qa(profile, sid, user=user, agent_id=agent_id)

        # Phase 3: Judge
        accuracy = run_judge(qa_results)
        if accuracy >= 0:
            assert accuracy >= 0.5, f"judge accuracy too low: {accuracy:.1%}"

        # Phase 4: Storage verification
        verify_ov_storage(profile, expected_entities=["小明", "咪咪"])

        # Summary
        log_summary(qa_results)
