"""
Remote 模式 E2E 测试 — clawhub 安装场景

与 local 模式的区别:
  - OpenViking 服务由测试框架预先启动（不由插件自动管理）
  - 启动前配置 root_api_key 开启 API key 认证
  - 通过 OV Admin API 注册租户/用户 → 获取用户 API key
  - 插件通过 HTTP API + API key 连接到外部 OV 服务
  - 交互式 setup 配置 baseUrl / apiKey / agentId(default)

测试流程:
  1. Setup: clawhub 安装插件 → 配置 root_api_key → 启动 OV → 注册租户获取 key
           → 交互式 setup (remote 模式)
  2. Pipeline: ingest → compact → QA → judge → verify
  3. Teardown: 停止 OV 服务 → 销毁 profile
"""

import json
import logging
import secrets
import time
import uuid

import pytest
import requests

from config.settings import (
    MODEL_PRIMARY,
    NODE_PATH,
    OPENVIKING_PORT,
    PLUGIN_VERSION,
    PROFILE_GATEWAY_PORT,
    PROFILE_NAME,
    get_effective_config,
)
from utils.process_manager import ProcessManager
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

TEST_ROOT_API_KEY = f"test-root-{secrets.token_hex(16)}"
TEST_ACCOUNT_ID = "default"
TEST_USER_ID = "default"


@pytest.mark.clawhub
@pytest.mark.remote
class TestRemoteE2ESingle:
    """Remote 模式 E2E: clawhub 安装 → OV 服务 → 注册租户 → 交互式配置 → ingest → QA → judge → verify。"""

    SESSION_ID = f"e2e-remote-{uuid.uuid4().hex[:8]}"
    profile: ProfileManager = None
    _ov_process = None
    _user_api_key: str = ""

    @classmethod
    def setup_class(cls):
        logger.info("=" * 60)
        logger.info("SETUP: remote mode profile '%s'", PROFILE_NAME)
        cfg = get_effective_config()
        logger.info("config: %s", json.dumps(cfg, indent=2, ensure_ascii=False))
        logger.info("=" * 60)

        cls.profile = ProfileManager()

        # 1) 创建 profile
        assert cls.profile.create(), "failed to create profile"

        # 2) 安装插件
        install_result = cls.profile.install_plugin()
        assert install_result["success"], f"plugin install failed: {install_result}"
        logger.info("plugin installed: %s", install_result.get("spec"))

        # 3) 创建隔离 ov.conf 并配置 root_api_key + bind 0.0.0.0（remote 场景）
        cls.profile.create_isolated_ov_conf()
        ov_conf = getattr(cls.profile, "_ov_conf", "")
        ov_port = getattr(cls.profile, "_ov_port", OPENVIKING_PORT + 100)

        with open(ov_conf) as f:
            ov_cfg = json.load(f)
        ov_cfg.setdefault("server", {})["root_api_key"] = TEST_ROOT_API_KEY
        ov_cfg["server"]["host"] = "0.0.0.0"
        with open(ov_conf, "w") as f:
            json.dump(ov_cfg, f, indent=2, ensure_ascii=False)
        logger.info("configured ov.conf: root_api_key set, host=0.0.0.0, port=%d", ov_port)

        # 4) 启动 OV 服务
        cls._start_ov_server(ov_conf, ov_port)

        # 5) 通过 Admin API 注册租户和用户 → 获取用户 API key
        base_url = f"http://127.0.0.1:{ov_port}"
        cls._user_api_key = cls._register_tenant_and_user(base_url)
        logger.info("registered user '%s' in account '%s', got API key",
                     TEST_USER_ID, TEST_ACCOUNT_ID)

        # 6) 运行 `openclaw openviking setup` 交互式配置 (remote 模式)
        setup_result = cls.profile.run_interactive_setup(
            mode="remote",
            base_url=base_url,
            api_key=cls._user_api_key,
            agent_id="default",
        )
        assert setup_result["success"], f"interactive setup failed: {setup_result}"
        logger.info("interactive setup completed: steps=%s",
                     [s["name"] for s in setup_result.get("steps", [])])

        # 6b) 打印 setup 后 openclaw.json 中的插件配置
        post_setup_cfg = cls.profile.read_config()
        plugin_cfg = post_setup_cfg.get("plugins", {}).get("entries", {}).get("openviking", {})
        logger.info("post-setup plugin config (written by setup command): %s",
                     json.dumps(plugin_cfg, ensure_ascii=False))

        # 7) 配置 auth / models / gateway
        cls.profile.configure_auth_from_default()
        cls.profile.configure_models()
        cls.profile.configure_gateway()

        # 8) 启动 gateway
        assert cls.profile.start_gateway(), "gateway failed to start"
        logger.info("gateway started on port %d", PROFILE_GATEWAY_PORT)

        # 9) 验证 OV 可访问
        time.sleep(3)
        assert ProcessManager.is_port_listening(ov_port), (
            f"OV server not listening on port {ov_port}"
        )
        logger.info("setup OK (remote OV at %s, auth=api_key)", base_url)

    @classmethod
    def teardown_class(cls):
        logger.info("TEARDOWN: destroying profile '%s'", PROFILE_NAME)
        if cls.profile:
            cls.profile.full_teardown()
        cls._stop_ov_server()

    @classmethod
    def _start_ov_server(cls, ov_conf: str, port: int):
        """启动独立的 OpenViking 服务器进程。"""
        if ProcessManager.is_port_listening(port):
            logger.info("OV already listening on port %d", port)
            return

        python_path = ProfileManager._resolve_openviking_python() or "python3"
        cmd = [
            python_path, "-m", "openviking.server.bootstrap",
            "--config", ov_conf,
        ]
        logger.info("starting OV server: %s", " ".join(cmd))

        log_path = f"/tmp/ov_remote_test_{port}.log"
        cls._ov_process = ProcessManager.start_background(
            cmd, log_path=log_path,
        )

        if not ProcessManager.wait_for_port(port, timeout=60):
            raise RuntimeError(f"OV server failed to start on port {port} (log: {log_path})")
        logger.info("OV server ready on port %d (pid=%d)", port, cls._ov_process.pid)

    @classmethod
    def _stop_ov_server(cls):
        """停止 OV 服务器。"""
        ov_port = getattr(cls.profile, "_ov_port", None) if cls.profile else None
        if ov_port and ProcessManager.is_port_listening(ov_port):
            ProcessManager.kill_by_port(ov_port, wait=3.0)
            logger.info("stopped OV server on port %d", ov_port)
        if cls._ov_process:
            try:
                cls._ov_process.terminate()
                cls._ov_process.wait(timeout=5)
            except Exception:
                pass

    @classmethod
    def _register_tenant_and_user(cls, base_url: str) -> str:
        """通过 OV Admin API 创建账户并注册用户，返回用户 API key。

        POST /api/v1/admin/accounts → 创建账户（同时返回 admin user key）
        """
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": TEST_ROOT_API_KEY,
        }

        # 创建账户（admin_user_id = TEST_USER_ID）
        resp = requests.post(
            f"{base_url}/api/v1/admin/accounts",
            json={
                "account_id": TEST_ACCOUNT_ID,
                "admin_user_id": TEST_USER_ID,
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("create account response: %s", json.dumps(data, ensure_ascii=False)[:300])

        user_key = data.get("result", {}).get("user_key", "")
        assert user_key, f"failed to get user_key from account creation: {data}"
        return user_key

    def test_e2e_flow(self):
        """完整 E2E 管线: ingest → sessions.compact → QA(独立session) → judge → verify。"""
        profile = self.__class__.profile
        sid = self.SESSION_ID
        user = "e2e-user"
        agent_id = "e2e-remote"

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
